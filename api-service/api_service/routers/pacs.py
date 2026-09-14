"""Authenticated PACS UI endpoints. Clinical metadata stays out of case tools."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api_service.deps import get_context, get_db
from api_service.helpers import log_event
from api_service.pacs import service
from api_service.pacs.client import PacsClient, PacsError
from api_service.runtime import settings
from backend_common.auth import AuthContext
from backend_common.db import PacsImport

router = APIRouter(prefix="/api/app/pacs", tags=["pacs"])


def operation(call):
    try:
        return call()
    except PacsError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/status")
def status(workspace_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    from api_service.helpers import get_workspace_for_user
    get_workspace_for_user(db, workspace_id, context.user.id)
    return {"enabled": settings.pacs_enabled and workspace_id in settings.pacs_workspace_ids.split(","), "source": settings.pacs_source_id}


@router.post("/studies/search")
def search(request: service.Search, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    return operation(lambda: service.search(db, context, request))


@router.get("/studies/{study_uid}/series")
def series(study_uid: str, workspace_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    service.authorize(db, context, workspace_id)
    db.commit()
    client = PacsClient(settings)
    try:
        result = operation(lambda: service.list_series(client, study_uid))
    finally:
        client.close()
    log_event(db, context, "pacs.study_inspected", details={"workspace_id": workspace_id})
    return result


@router.post("/imports", status_code=202)
def create(request: service.ImportRequest, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    with service.submission_lock:
        return operation(lambda: service.submit(db, context, request))


@router.get("/imports")
def imports(workspace_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    service.authorize(db, context, workspace_id)
    return [service.serialize(row) for row in db.query(PacsImport).filter_by(workspace_id=workspace_id).order_by(PacsImport.created_at.desc()).limit(50)]


@router.get("/imports/{import_id}")
def progress(import_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    return service.serialize(service.get_import(db, context, import_id))


@router.get("/cases/{case_id}")
def provenance(case_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    from api_service.helpers import get_case_for_user
    get_case_for_user(db, case_id, context.user.id)
    row = db.query(PacsImport).filter_by(case_id=case_id).first()
    return service.serialize(row) if row else None


@router.post("/imports/{import_id}/cancel")
def cancel(import_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    return service.cancel_import(db, context, import_id)


@router.post("/imports/{import_id}/retry", status_code=202)
def retry(import_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    return service.retry_import(db, context, import_id)
