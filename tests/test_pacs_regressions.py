"""Regression probes for the PACS code/architecture review findings."""
# ruff: noqa: F811
import shutil

import numpy as np
import pytest
from api_service.pacs import compatibility, service, worker
from api_service.pacs.client import PacsError
from api_service.pacs.outputs import fingerprint, prepare_outputs, sequence_type
from api_service.runtime_tools.workflow_catalog import WorkflowInput
from fastapi import HTTPException
from nibabel.nifti1 import Nifti1Image
from test_pacs import context  # noqa: F401

from backend_common.case_storage import case_storage_dir
from backend_common.db import Artifact, ArtifactKind, PacsImport


def row_for(db, **kwargs):
    row = PacsImport(id="import", workspace_id="workspace", case_id="case", user_id="user", source_id="source",
                     study_uid="1.2", submission_key="key", job_id="attempt", **kwargs)
    db.add(row)
    db.commit()
    return row


def test_cancel_refreshes_stale_queued_state_and_blocks_retry(context, monkeypatch):
    db, auth, factory = context
    row = row_for(db, state="queued", series_json=[{"series_uid": "1.3", "state": "queued", "modality": "MR"}])
    monkeypatch.setattr(service.job_manager, "cancel", lambda _: True)

    class Client:
        def __init__(self, *_):
            pass

        def close(self):
            pass

        def instances(self, *_):
            # The request session still has queued cached; worker has claimed it.
            assert row.state == "queued"
            assert service.cancel_import(db, auth, row.id)["state"] == "canceling"
            with pytest.raises(HTTPException) as error:
                service.retry_import(db, auth, row.id)
            assert error.value.status_code == 409
            raise PacsError("canceled")

    monkeypatch.setattr(worker, "PacsClient", Client)
    worker.run_import(row.id, "attempt")
    db.expire_all()
    assert row.state == "canceled"


def test_stale_attempt_cannot_run_or_remove_staging(context):
    db, _, _ = context
    row = row_for(db, state="queued")
    root = service.settings.fs_data_root / ".tmp/pacs-imports/import"
    root.mkdir(parents=True)
    (root / "new-attempt").write_text("keep")
    worker.run_import(row.id, "old-attempt")
    db.expire_all()
    assert row.state == "queued"
    assert (root / "new-attempt").exists()


@pytest.mark.parametrize("state", ["interrupted", "failed", "canceled", "completed_with_errors"])
def test_recovery_reconciles_terminal_uncommitted_outputs(context, state):
    db, _, _ = context
    row_for(db, state=state, series_json=[{"state": "completed"}, {"state": "failed"}])
    root = case_storage_dir(service.settings, "workspace", "case")
    for index in (0, 1):
        directory = root / f"pacs-series-{index}"
        directory.mkdir()
        (directory / "test.nii").write_bytes(b"data")
    worker.recover_imports()
    assert (root / "pacs-series-0/test.nii").exists()
    assert not (root / "pacs-series-1").exists()


def test_publication_excludes_unpaired_files_and_sanitizes_values(tmp_path):
    converted = tmp_path / "converted"
    converted.mkdir()
    Nifti1Image(np.zeros((3, 3, 3)), np.eye(4)).to_filename(converted / "scan.nii")
    (converted / "scan.json").write_text('{"PatientName":"secret","EchoTime":"secret","RepetitionTime":2}')
    (converted / "unpaired.json").write_text('{"PatientName":"secret"}')
    (converted / "unexpected.txt").write_text("secret")
    published = tmp_path / "published"
    profiles = prepare_outputs(converted, published, 100000, {"modality": "MR", "description": "T2"})
    assert sorted(p.name for p in published.iterdir()) == ["volume-0.json", "volume-0.nii"]
    assert "secret" not in (published / "volume-0.json").read_text()
    assert profiles["volume-0.nii"]["sequence"] == "other"


def test_truncated_voxel_payload_rejected(tmp_path):
    converted = tmp_path / "converted"
    converted.mkdir()
    path = converted / "scan.nii"
    Nifti1Image(np.zeros((10, 10, 10)), np.eye(4)).to_filename(path)
    with path.open("r+b") as stream:
        stream.truncate(400)
    with pytest.raises((ValueError, OSError)):
        prepare_outputs(converted, tmp_path / "published", 100000, {"modality": "MR"})


def test_database_provenance_blocks_copied_t2_and_checks_actual_dimensions(context, monkeypatch):
    db, _, factory = context
    monkeypatch.setattr(compatibility, "SessionLocal", factory)
    root = case_storage_dir(service.settings, "workspace", "case")
    source = root / "scan.nii"
    Nifti1Image(np.zeros((3, 3, 3, 2)), np.eye(4)).to_filename(source)
    metadata = {"source": "pacs", "modality": "MR", "dimensions": 3, "sequence": "other", "sha256": fingerprint(source)}
    artifact = Artifact(workspace_id="workspace", case_id="case", kind=ArtifactKind.volume, name="scan.nii", relative_path="scan.nii", metadata_json=metadata)
    db.add(artifact)
    db.commit()
    copied = root / "copied.nii"
    shutil.copyfile(source, copied)
    requirement = WorkflowInput(name="t1", description="T1", modalities=["MR"], sequences=["T1"])
    with pytest.raises(ValueError, match="sequence"):
        compatibility.validate_input(copied, requirement)
    requirement.sequences = []
    requirement.dimensions = [3]
    with pytest.raises(ValueError, match="dimensions"):
        compatibility.validate_input(copied, requirement)
    # Removing/forging the old marker cannot alter database policy.
    (root / ".pacs-inputs.json").write_text('{"copied.nii":{"modality":"MR","dimensions":3}}')
    with pytest.raises(ValueError, match="dimensions"):
        compatibility.validate_input(copied, requirement)


def test_cleanup_failure_blocks_startup_and_case_mutation(context, monkeypatch):
    from api_service.cases.service import ensure_case_not_active

    from backend_common.db import Case
    db, _, _ = context
    row = row_for(db, state="interrupted", series_json=[{"state": "failed"}])
    monkeypatch.setattr(worker, "cleanup_destinations", lambda _: False)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        worker.recover_imports()
    db.expire_all()
    assert row.error_code == "cleanup_failed"
    with pytest.raises(HTTPException):
        ensure_case_not_active(db, db.get(Case, "case"))


@pytest.mark.parametrize("label", ["T1_CE", "T1+C", "T1_Gd", "T1w + C", "T1 postcontrast", "T2", "FLAIR"])
def test_descriptions_never_verify_native_t1(label):
    assert sequence_type(label) == "other"
    assert sequence_type("3D T1 MPRAGE") == "T1_candidate"


def test_other_workspace_cannot_change_local_compatibility(context, monkeypatch):
    from backend_common.db import Workspace
    db, _, factory = context
    monkeypatch.setattr(compatibility, "SessionLocal", factory)
    root = case_storage_dir(service.settings, "workspace", "case")
    path = root / "native.nii"
    Nifti1Image(np.zeros((3, 3, 3)), np.eye(4)).to_filename(path)
    metadata = {"source": "pacs", "modality": "MR", "sequence": "T1", "sha256": fingerprint(path)}
    db.add(Artifact(workspace_id="workspace", case_id="case", kind=ArtifactKind.volume, name=path.name, relative_path=path.name, metadata_json=metadata))
    db.add(Workspace(id="other", owner_user_id="user", name="other"))
    db.flush()
    db.add(Artifact(workspace_id="other", kind=ArtifactKind.volume, name=path.name, relative_path=path.name,
                    metadata_json={**metadata, "sequence": "unknown"}))
    db.commit()
    compatibility.validate_input(path, WorkflowInput(name="t1", description="T1", sequences=["T1"]))


def test_discovery_lock_does_not_block_cancellation(context, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    db, auth, factory = context
    row = row_for(db, state="running")
    monkeypatch.setattr(service.job_manager, "cancel", lambda _: True)

    def cancel():
        with factory() as session:
            return service.cancel_import(session, auth, row.id)["state"]

    with ThreadPoolExecutor(max_workers=1) as pool, service.submission_lock:
        assert pool.submit(cancel).result(timeout=2) == "canceling"


def test_attestation_persists_and_is_part_of_idempotency(context, monkeypatch):
    db, auth, _ = context

    class Client:
        def __init__(self, *_):
            pass

        def close(self):
            pass

        def query(self, *_):
            return [{"0020000D": {"Value": ["1.2"]}}]

    monkeypatch.setattr(service, "PacsClient", Client)
    monkeypatch.setattr(service, "list_series", lambda *_: [{"series_uid": "1.3", "eligible": True, "modality": "MR", "description": "native T1", "count": 1, "state": "queued"}])
    calls = []
    monkeypatch.setattr(service.job_manager, "submit", lambda *args, **kwargs: calls.append(kwargs))
    request = service.ImportRequest(workspace_id="workspace", study_uid="1.2", series_uids=["1.3"], verified_t1_series_uids=["1.3"], submission_key="verified")
    result = service.submit(db, auth, request)
    assert result["series"][0]["verified_native_t1"] is True
    assert result["series"][0]["verified_by"] == auth.user.id
    assert service.submit(db, auth, request)["id"] == result["id"]
    assert len(calls) == 1
    request.verified_t1_series_uids = []
    with pytest.raises(HTTPException) as error:
        service.submit(db, auth, request)
    assert error.value.status_code == 409


def test_attested_contrast_agent_rejected_before_conversion(context, monkeypatch):
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian
    db, _, _ = context

    class Client:
        def __init__(self, *_):
            pass

        def close(self):
            pass

        def instances(self, *_):
            return [{"00080018": {"Value": ["1.3.1"]}}]

        def retrieve(self, study, series, instance, path, check):
            header = FileDataset(str(path), {}, file_meta=FileMetaDataset(), preamble=b"\0" * 128)
            header.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            header.ContrastBolusAgent = "Gadobutrol"
            header.save_as(path)
            check(path.stat().st_size)
            return path.stat().st_size, "checksum"

    monkeypatch.setattr(worker, "PacsClient", Client)
    conversions = []
    monkeypatch.setattr(worker, "run_dcm2niix", lambda *_: conversions.append(True))
    row = row_for(db, state="queued", series_json=[{"series_uid": "1.3", "state": "queued", "modality": "MR", "verified_native_t1": True}])
    worker.run_import(row.id, "attempt")
    db.expire_all()
    assert row.state == "failed"
    assert row.series_json[0]["error_code"] == "incompatible_sequence"
    assert not conversions
