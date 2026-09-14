"""Series-level conversion and recovery for durable PACS imports."""

from __future__ import annotations

import os
import shutil
import threading

import pydicom
from neurocade_runtime_tools.container_request import DCM2NIIX_IMAGE
from neurocade_runtime_tools.execution import cancellation_observer

from api_service.pacs.client import IMAGE_CLASSES, PacsClient, PacsError, value
from api_service.pacs.outputs import prepare_outputs
from api_service.pacs.service import ACTIVE, lifecycle_lock
from api_service.runtime import settings
from api_service.runtime.dicom_conversion import run_dcm2niix
from backend_common.case_storage import case_storage_dir
from backend_common.db import Artifact, ArtifactKind, AuditEvent, PacsImport, SessionLocal, WorkspaceMembership


def convert_bounded(incoming, converted, remaining_bytes):
    """Stop conversion when its output exceeds the configured disk budget."""
    stopped = threading.Event()
    exceeded = threading.Event()
    callbacks = []
    parent_observer = cancellation_observer.get()

    def observe(callback):
        callbacks.append(callback)
        if parent_observer:
            parent_observer(callback)
        if exceeded.is_set():
            callback()

    def monitor():
        while not stopped.wait(0.5):
            try:
                size = sum(path.stat().st_size for path in converted.rglob("*") if path.is_file())
                if size <= remaining_bytes and shutil.disk_usage(converted).free >= 512 * 1024 * 1024:
                    continue
                exceeded.set()
                for callback in callbacks:
                    callback()
                return
            except OSError:
                continue

    token = cancellation_observer.set(observe)
    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        run_dcm2niix(incoming, converted)
        if sum(path.stat().st_size for path in converted.rglob("*") if path.is_file()) > remaining_bytes:
            raise PacsError("conversion_disk_limit")
    finally:
        stopped.set()
        watcher.join(timeout=1)
        cancellation_observer.reset(token)
        if exceeded.is_set():
            raise PacsError("conversion_disk_limit")


def check_access(db, row):
    if row.cancel_requested:
        raise PacsError("canceled")
    if row.source_id != settings.pacs_source_id:
        raise PacsError("source_changed")
    if not settings.pacs_enabled or row.workspace_id not in settings.pacs_workspace_ids.split(","):
        raise PacsError("access_revoked")
    if not db.query(WorkspaceMembership).filter_by(workspace_id=row.workspace_id, user_id=row.user_id).first():
        raise PacsError("access_revoked")


def run_import(import_id: str, attempt_id: str | None = None):
    root = settings.fs_data_root / ".tmp" / "pacs-imports" / import_id
    client = PacsClient(settings)
    total = 0
    claimed = False
    final_state = "interrupted"
    final_error = "processing_interrupted"
    try:
        with lifecycle_lock, SessionLocal() as db:
            row = db.get(PacsImport, import_id)
            if row is None or row.state != "queued" or row.cancel_requested or (attempt_id is not None and row.job_id != attempt_id):
                return
            attempt_id = row.job_id
            row.state = "running"
            db.commit()
            claimed = True
            series = [dict(item) for item in row.series_json]
            study = row.study_uid
            identity = dict(row.provenance_json)
            case_dir = case_storage_dir(settings, row.workspace_id, row.case_id)

        def check(size=0):
            nonlocal total
            total += size
            if total > settings.pacs_max_import_bytes:
                raise PacsError("import_size_limit")
            with SessionLocal() as db:
                current = db.get(PacsImport, import_id)
                if current is None or current.job_id != attempt_id:
                    raise PacsError("canceled")
                check_access(db, current)
            if root.exists() and shutil.disk_usage(root).free < 512 * 1024 * 1024:
                raise PacsError("disk_space_limit")

        def save():
            with lifecycle_lock, SessionLocal() as db:
                current = db.get(PacsImport, import_id)
                if current is None or current.job_id != attempt_id:
                    raise PacsError("canceled")
                current.series_json = [dict(item) for item in series]
                db.commit()

        for index, item in enumerate(series):
            if item["state"] == "completed":
                continue
            stage = root / str(index)
            try:
                check()
                stage.mkdir(parents=True, exist_ok=True)
                incoming, converted = stage / "input", stage / "output"
                incoming.mkdir(exist_ok=True)
                converted.mkdir(exist_ok=True)
                item["state"] = "retrieving"
                item.pop("error_code", None)
                save()
                manifest = client.instances(study, item["series_uid"])
                expected = {str(value(entry, "00080018")) for entry in manifest}
                received = []
                for number, entry in enumerate(manifest):
                    instance_uid = str(value(entry, "00080018"))
                    path = incoming / f"{number}.dcm"
                    size, checksum = client.retrieve(study, item["series_uid"], instance_uid, path, check)
                    header = pydicom.dcmread(path, stop_before_pixels=True)
                    if item.get("verified_native_t1") and str(header.get("ContrastBolusAgent", "")).strip():
                        raise PacsError("incompatible_sequence")
                    if str(header.get("SOPClassUID", "")) not in IMAGE_CLASSES:
                        raise PacsError("unsupported_sop_class")
                    if str(header.get("StudyInstanceUID", "")) != study or str(header.get("SeriesInstanceUID", "")) != item["series_uid"] or str(header.get("SOPInstanceUID", "")) != instance_uid:
                        raise PacsError("identity_mismatch")
                    for field, tag in (("patient_id", "PatientID"), ("patient_id_issuer", "IssuerOfPatientID"), ("patient_name", "PatientName"), ("birth_date", "PatientBirthDate")):
                        actual = str(header.get(tag, ""))
                        if field == "patient_name":
                            actual = actual.split("=")[0].rstrip("^ ")
                        expected_identity = identity.get(field, "")
                        if field == "patient_name":
                            expected_identity = expected_identity.rstrip("^ ")
                        if expected_identity and actual != expected_identity:
                            raise PacsError("identity_mismatch")
                    received.append({"uid": instance_uid, "sha256": checksum, "bytes": size, "transfer_syntax": str(header.file_meta.get("TransferSyntaxUID", ""))})
                    item["retrieved_count"] = number + 1
                    item["count"] = len(manifest)
                    save()
                if {str(value(entry, "00080018")) for entry in client.instances(study, item["series_uid"])} != expected:
                    raise PacsError("series_changed")
                item["manifest"] = received
                item["state"] = "converting"
                save()
                check()
                stored_bytes = sum(path.stat().st_size for path in case_dir.glob("pacs-series-*/*") if path.is_file())
                remaining_bytes = settings.pacs_max_import_bytes - stored_bytes
                if remaining_bytes <= 0:
                    raise PacsError("output_size_limit")
                convert_bounded(incoming, converted, remaining_bytes)
                check()
                published = stage / "published"
                input_profiles = prepare_outputs(converted, published, remaining_bytes, item)
                destination = case_dir / f"pacs-series-{index}"
                # A whole-series directory is promoted before one artifact transaction.
                # Recovery removes any directory whose series wasn't committed.
                if destination.exists():
                    shutil.rmtree(destination)
                published.rename(destination)
                with lifecycle_lock, SessionLocal() as db:
                    current = db.get(PacsImport, import_id)
                    if current is None or current.job_id != attempt_id:
                        raise PacsError("canceled")
                    check_access(db, current)
                    for path in destination.iterdir():
                        if path.name.startswith("."):
                            continue
                        is_volume = path.name.endswith((".nii", ".nii.gz"))
                        db.add(Artifact(case_id=current.case_id, workspace_id=current.workspace_id,
                                        kind=ArtifactKind.volume if is_volume else ArtifactKind.derived,
                                        name=path.name, relative_path=str(path.relative_to(case_dir)), size_bytes=path.stat().st_size,
                                        metadata_json={"source": "pacs", "volume_role": "intensity" if is_volume else "metadata", "pacs_import_id": import_id,
                                                       "modality": item["modality"], "series_uid": item["series_uid"], "study_uid": study,
                                                       **input_profiles.get(path.name, {})}))
                    item["state"] = "completed"
                    current.provenance_json = {**current.provenance_json, "converter_image": os.environ.get("NEUROCADE_DCM2NIIX_IMAGE", DCM2NIIX_IMAGE), "conversion_options": "-z y -b y -ba y -c empty -f %p_%s"}
                    current.series_json = [dict(entry) for entry in series]
                    db.commit()
            except Exception as exc:
                item["state"] = "canceled" if isinstance(exc, PacsError) and str(exc) == "canceled" else "failed"
                item["error_code"] = str(exc) if isinstance(exc, PacsError) else "conversion_failed"
                save()
                destination = case_dir / f"pacs-series-{index}"
                if destination.exists():
                    shutil.rmtree(destination)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        with SessionLocal() as db:
            current = db.get(PacsImport, import_id)
            if current is None:
                return
            count = sum(item["state"] == "completed" for item in series)
            final_state = "completed" if count == len(series) else "completed_with_errors" if count else "failed"
            final_error = None
    except Exception:
        pass  # Terminal acknowledgement happens only after all filesystem cleanup.
    finally:
        client.close()
        cleanup_failed = False
        if claimed and root.exists():
            try:
                shutil.rmtree(root)
            except OSError:
                cleanup_failed = True
        if claimed:
            with lifecycle_lock, SessionLocal() as db:
                current = db.get(PacsImport, import_id)
                if current and current.job_id == attempt_id:
                    # Reconcile failed promotions even when the inner cleanup failed.
                    cleanup_failed |= not cleanup_destinations(current)
                    current.state = "interrupted" if cleanup_failed else "canceled" if current.cancel_requested else final_state
                    current.error_code = "cleanup_failed" if cleanup_failed else final_error
                    db.add(AuditEvent(user_id=current.user_id, case_id=current.case_id, action="pacs.finished", details_json={"import_id": import_id, "state": current.state}))
                    db.commit()


def cleanup_destinations(row):
    success = True
    for index, item in enumerate(row.series_json):
        if item["state"] == "completed":
            continue
        destination = case_storage_dir(settings, row.workspace_id, row.case_id) / f"pacs-series-{index}"
        try:
            if destination.exists():
                shutil.rmtree(destination)
        except OSError:
            success = False
            # If deletion fails, at least quarantine outside user-visible cases.
            quarantine = settings.fs_data_root / ".tmp" / "pacs-imports" / row.id / "quarantine" / str(index)
            try:
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(quarantine)
            except OSError:
                pass
    return success


def recover_imports():
    """Recover incomplete promotions before artifact reconciliation and dispatch."""
    with SessionLocal() as db:
        for row in db.query(PacsImport).all():
            if not cleanup_destinations(row):
                row.state = "interrupted"
                row.error_code = "cleanup_failed"
                continue
            root = settings.fs_data_root / ".tmp" / "pacs-imports" / row.id
            if root.exists():
                try:
                    shutil.rmtree(root)
                except OSError:
                    row.state = "interrupted"
                    row.error_code = "cleanup_failed"
                    continue
            if row.state not in ACTIVE:
                if row.error_code == "cleanup_failed":
                    row.error_code = "process_interrupted"
                continue
            from backend_common.db import BackgroundJob
            job = db.get(BackgroundJob, row.job_id) if row.job_id else None
            if row.state == "queued" and job is not None and job.state == "queued":
                continue
            row.state = "interrupted"
            row.error_code = "process_interrupted"
            series = [dict(item) for item in row.series_json]
            for index, item in enumerate(series):
                if item["state"] != "completed":
                    item["state"] = "interrupted"
                    destination = case_storage_dir(settings, row.workspace_id, row.case_id) / f"pacs-series-{index}"
                    if destination.exists():
                        shutil.rmtree(destination)
            row.series_json = series
            root = settings.fs_data_root / ".tmp" / "pacs-imports" / row.id
            if root.exists():
                shutil.rmtree(root)
        db.commit()
        if db.query(PacsImport).filter_by(error_code="cleanup_failed").first():
            raise RuntimeError("PACS cleanup failed; repair storage permissions/free space before restarting")
