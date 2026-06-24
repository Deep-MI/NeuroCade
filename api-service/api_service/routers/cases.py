"""Provide API service cases behavior for NeuroCade."""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from api_service.cases.operations import (
    add_upload_to_case,
    cancel_active_case_run,
    create_case_from_upload,
    delete_case_for_user,
    get_case_detail_for_user,
    get_case_logs_for_user,
    list_case_runs_for_user,
    list_visible_cases,
    save_generated_case_volume,
    start_fastsurfer_run,
    update_case_metadata,
)
from api_service.deps import get_context, get_db
from api_service.helpers import get_workspace_for_user
from api_service.policies import require_workspace_manage
from api_service.runtime.service import runtime_service
from api_service.schemas import ArtifactSummary, RunSummary, CaseDetail, CaseRenameRequest, CaseRenameResponse, CaseSummary, StartRunRequest, UploadResponse
from backend_common.auth import AuthContext


router = APIRouter(prefix="/api/app", tags=["cases"])


@router.get("/cases", response_model=list[CaseSummary])
async def list_cases(
    workspace_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[CaseSummary]:
    """List active cases visible to the current user, optionally scoped to a workspace."""
    return await list_visible_cases(db, context, workspace_id=workspace_id)


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case_detail(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> CaseDetail:
    """Return case metadata, artifacts, runs, and assistant thread details."""
    return get_case_detail_for_user(db, context, case_id=case_id)


@router.post("/cases", response_model=UploadResponse)
async def create_case_with_upload(
    workspace_id: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    modalities: str | None = Form(None),
    tags: str | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Create a case from uploaded input files."""
    return await create_case_from_upload(
        db,
        context,
        workspace_id=workspace_id,
        title=title,
        description=description,
        modalities=modalities,
        tags=tags,
        notes=notes,
        file=file,
        files=files,
    )


@router.post("/cases/{case_id}/uploads", response_model=UploadResponse)
async def add_case_upload(
    case_id: str,
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Attach uploaded input files to an existing case."""
    return await add_upload_to_case(db, context, case_id=case_id, file=file, files=files)


@router.post("/cases/{case_id}/generated-volume", response_model=ArtifactSummary)
async def save_generated_volume(
    case_id: str,
    filename: str = Form(...),
    metadata: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> ArtifactSummary:
    """Save a generated viewer volume as a case artifact."""
    return await save_generated_case_volume(db, context, case_id=case_id, filename=filename, metadata=metadata, file=file)


@router.post("/runs", response_model=RunSummary)
async def start_run(
    request: StartRunRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> RunSummary:
    """Create and submit a FastSurfer run for a case."""
    return await start_fastsurfer_run(db, context, request=request)


@router.get("/cases/{case_id}/runs", response_model=list[RunSummary])
async def case_runs(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[RunSummary]:
    """List runs for a case, syncing the latest runtime status first."""
    return await list_case_runs_for_user(db, context, case_id=case_id)


@router.get("/cases/{case_id}/logs", response_model=dict)
async def case_logs(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Return live runtime logs or stored logs for the latest case run."""
    return await get_case_logs_for_user(db, context, case_id=case_id)


@router.get("/queue-status", response_model=dict)
async def queue_status(
    workspace_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Return runtime queue status for workspace managers."""
    _workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_manage(role, detail="Only owners/admins can inspect global queue status")
    return await runtime_service.fetch_queue_status()


@router.post("/cases/{case_id}/cancel", response_model=dict)
async def cancel_case_run(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Cancel the active runtime job for a case and mark its run canceled."""
    return await cancel_active_case_run(db, context, case_id=case_id)


@router.patch("/cases/{case_id}", response_model=CaseRenameResponse)
def rename_case(
    case_id: str,
    request: CaseRenameRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> CaseRenameResponse:
    """Update a case title and editable metadata fields."""
    return update_case_metadata(db, context, case_id=case_id, request=request)


@router.delete("/cases/{case_id}", response_model=dict)
async def delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Delete a case and all of its stored data."""
    return delete_case_for_user(db, context, case_id=case_id)
