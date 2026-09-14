"""Authorization, discovery and durable import submission."""

from __future__ import annotations

from datetime import date
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from api_service.helpers import get_workspace_for_user, log_event
from api_service.jobs import job_manager
from api_service.pacs.client import IMAGE_CLASSES, PacsClient, PacsError, uid, value
from api_service.policies import require_workspace_write
from api_service.runtime import settings
from backend_common.case_storage import ensure_case_storage_layout
from backend_common.db import Case, PacsImport
from backend_common.output_activity import OutputBusy, ensure_outputs_idle
from backend_common.submission_lock import submission_lock as output_submission_lock

ACTIVE = ("queued", "running", "canceling")
TASK = "api_service.pacs.import"
submission_lock = RLock()
lifecycle_lock = RLock()


class Search(BaseModel):
    workspace_id: str
    patient_id: str = Field(default="", max_length=64)
    accession: str = Field(default="", max_length=64)
    patient_name: str = Field(default="", max_length=128)
    date_from: date | None = None
    date_to: date | None = None
    modality: str = Field(default="", max_length=16, pattern=r"^[A-Z]*$")
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def bounded(self):
        for item in (self.patient_id, self.accession):
            if any(c in item for c in "*?\\"):
                raise ValueError("Patient ID and accession must be exact identifiers")
        if bool(self.date_from) != bool(self.date_to):
            raise ValueError("Supply both dates")
        if self.date_from and self.date_to and not 0 <= (self.date_to - self.date_from).days <= 30:
            raise ValueError("Date range must be 1–31 days")
        if not (self.patient_id.strip() or self.accession.strip() or self.date_from):
            raise ValueError("Supply an exact patient ID, accession, or bounded date range")
        return self


class ImportRequest(BaseModel):
    workspace_id: str
    study_uid: str
    series_uids: list[str] = Field(min_length=1, max_length=100)
    submission_key: str = Field(min_length=1, max_length=128)
    confirm_duplicate: bool = False
    verified_t1_series_uids: list[str] = Field(default_factory=list, max_length=100)


def authorize(db, context, workspace_id):
    workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_write(role)
    if not settings.pacs_enabled or workspace_id not in settings.pacs_workspace_ids.split(","):
        raise HTTPException(403, "PACS is not enabled for this workspace")
    return workspace


def study_summary(data):
    tags = {"study_uid": "0020000D", "patient_name": "00100010", "patient_id": "00100020", "patient_id_issuer": "00100021",
            "birth_date": "00100030", "sex": "00100040", "accession": "00080050", "study_date": "00080020", "description": "00081030"}
    return {key: str(value(data, tag)) for key, tag in tags.items()}


def search(db, context, request):
    authorize(db, context, request.workspace_id)
    db.commit()
    params = {"limit": request.limit, "offset": request.offset, "includefield": "all"}
    for key, val in (("PatientID", request.patient_id), ("AccessionNumber", request.accession), ("PatientName", request.patient_name), ("ModalitiesInStudy", request.modality)):
        if val:
            params[key] = val
    if request.date_from:
        params["StudyDate"] = f"{request.date_from:%Y%m%d}-{request.date_to:%Y%m%d}"
    client = PacsClient(settings)
    try:
        results = [study_summary(item) for item in client.query("/studies", params)][:request.limit]
    finally:
        client.close()
    log_event(db, context, "pacs.searched", details={"workspace_id": request.workspace_id, "count": len(results)})
    return results


def list_series(client, study_uid):
    results = []
    while True:
        batch = client.query(f"/studies/{uid(study_uid)}/series", {"limit": 50, "offset": len(results)})
        if not batch:
            break
        results.extend(batch)
        if len(results) > settings.pacs_max_series:
            raise PacsError("series_limit")
        if len({value(item, "0020000E") for item in results}) != len(results):
            raise PacsError("invalid_series_manifest")
    summaries = []
    for item in results:
        series_uid = uid(str(value(item, "0020000E")))
        instances = client.instances(study_uid, series_uid)
        eligible = all(value(instance, "00080016") in IMAGE_CLASSES for instance in instances)
        summaries.append({"series_uid": series_uid, "description": str(value(item, "0008103E")), "modality": str(value(item, "00080060")),
                          "eligible": eligible, "reason": "" if eligible else "Unsupported DICOM object type", "count": len(instances), "state": "queued"})
    return summaries


def serialize(row):
    return {"id": row.id, "case_id": row.case_id, "workspace_id": row.workspace_id, "state": row.state,
            "provenance": row.provenance_json, "series": [{k: v for k, v in item.items() if k != "manifest"} for item in row.series_json], "error_code": row.error_code}


def submit(db, context, request):
    workspace = authorize(db, context, request.workspace_id)
    existing = db.query(PacsImport).filter_by(workspace_id=workspace.id, submission_key=request.submission_key).first()
    if existing:
        if (existing.study_uid != request.study_uid or {item["series_uid"] for item in existing.series_json} != set(request.series_uids)
                or {item["series_uid"] for item in existing.series_json if item.get("verified_native_t1")} != set(request.verified_t1_series_uids)):
            raise HTTPException(409, "Submission key was used for a different import")
        return serialize(existing)
    duplicates = db.query(PacsImport).filter_by(workspace_id=workspace.id, source_id=settings.pacs_source_id, study_uid=request.study_uid).all()
    if duplicates and not request.confirm_duplicate:
        raise HTTPException(409, {"code": "duplicate_study", "case_ids": [item.case_id for item in duplicates]})
    if db.query(PacsImport).filter(PacsImport.state.in_(ACTIVE)).count() >= settings.pacs_max_pending:
        raise HTTPException(429, "PACS import queue is full")
    db.commit()
    client = PacsClient(settings)
    try:
        studies = client.query("/studies", {"StudyInstanceUID": uid(request.study_uid), "limit": 2})
        if len(studies) != 1 or value(studies[0], "0020000D") != request.study_uid:
            raise PacsError("study_not_found")
        series = list_series(client, request.study_uid)
    finally:
        client.close()
    selected = set(request.series_uids)
    chosen = [item for item in series if item["series_uid"] in selected and item["eligible"]]
    if len(chosen) != len(selected):
        raise HTTPException(422, "Select only eligible series from this study")
    if sum(item["count"] for item in chosen) > settings.pacs_max_instances:
        raise HTTPException(413, "Import exceeds the instance limit")
    from api_service.pacs.outputs import sequence_type
    reviewed = set(request.verified_t1_series_uids)
    if not reviewed.issubset(selected):
        raise HTTPException(422, "T1 verification must refer to selected series")
    for item in chosen:
        if item["series_uid"] in reviewed:
            if item["modality"] != "MR" or sequence_type(item["description"]) == "other":
                raise HTTPException(422, "Series is not compatible with native T1 verification")
            item["verified_native_t1"] = True
            item["verified_by"] = context.user.id
    with lifecycle_lock, output_submission_lock:
        return persist_submission(db, context, request, chosen, studies[0])


def persist_submission(db, context, request, chosen, study):
    db.expire_all()
    workspace = authorize(db, context, request.workspace_id)
    if db.query(PacsImport).filter(PacsImport.state.in_(ACTIVE)).count() >= settings.pacs_max_pending:
        raise HTTPException(429, "PACS import queue is full")
    identifier = str(uuid4())
    case = Case(id=str(uuid4()), workspace_id=workspace.id, owner_user_id=context.user.id, title=f"pacs-{identifier[:12]}")
    try:
        ensure_outputs_idle(db, workspace.id, case.id)
    except OutputBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(case)
    db.flush()
    ensure_case_storage_layout(settings, case, workspace)
    row = PacsImport(id=identifier, case_id=case.id, workspace_id=workspace.id, user_id=context.user.id,
                     source_id=settings.pacs_source_id, study_uid=request.study_uid, submission_key=request.submission_key,
                     provenance_json=study_summary(study), series_json=chosen, job_id=str(uuid4()))
    db.add(row)
    db.commit()
    dispatch(db, row)
    log_event(db, context, "pacs.submitted", case_id=case.id, details={"import_id": identifier})
    return serialize(row)


def dispatch(db, row):
    try:
        job_manager.submit(TASK, {"import_id": row.id, "attempt_id": row.job_id}, queue="pacs-import", job_id=row.job_id)
    except Exception:
        row.state = "interrupted"
        row.error_code = "dispatch_failed"
        db.commit()


def get_import(db, context, import_id):
    row = db.get(PacsImport, import_id, populate_existing=True)
    if row is None:
        raise HTTPException(404, "Import not found")
    authorize(db, context, row.workspace_id)
    return row


def cancel_import(db, context, import_id):
    # Claim, cancellation and terminal acknowledgement share the monolith lock.
    # Never publish a terminal state for a worker that has claimed the import.
    with lifecycle_lock:
        row = get_import(db, context, import_id)
        if row.state in ACTIVE:
            row.cancel_requested = True
            row.state = "canceled" if row.state == "queued" else "canceling"
            db.commit()
            if row.job_id:
                job_manager.cancel(row.job_id)
            log_event(db, context, "pacs.canceled", case_id=row.case_id, details={"import_id": row.id})
        return serialize(row)


def retry_import(db, context, import_id):
    with lifecycle_lock, output_submission_lock:
        row = get_import(db, context, import_id)
        if row.state in (*ACTIVE, "completed"):
            raise HTTPException(409, "Import cannot be retried in its current state")
        if row.job_id and job_manager.status(row.job_id)["status"] in ("queued", "running"):
            raise HTTPException(409, "Previous import worker has not exited")
        if row.error_code == "cleanup_failed":
            raise HTTPException(409, "Import cleanup must finish before retry")
        if db.query(PacsImport).filter(PacsImport.state.in_(ACTIVE)).count() >= settings.pacs_max_pending:
            raise HTTPException(429, "PACS import queue is full")
        from api_service.cases.service import ensure_case_not_active
        ensure_case_not_active(db, db.get(Case, row.case_id))
        row.state = "queued"
        row.cancel_requested = False
        row.error_code = None
        row.job_id = str(uuid4())
        db.commit()
        dispatch(db, row)
        log_event(db, context, "pacs.retried", case_id=row.case_id, details={"import_id": row.id})
        return serialize(row)
