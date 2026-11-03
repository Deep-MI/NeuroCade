"""Provide API service cases behavior for NeuroCade."""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from api_service.cases.operations import (
    add_upload_to_case,
    create_case_from_linked_source,
    create_case_from_upload,
    delete_case_for_user,
    save_generated_case_volume,
    update_case_metadata,
)
from api_service.cases.queries import (
    get_case_detail_for_user,
    get_case_logs_for_user,
    list_case_runs_for_user,
    list_visible_cases,
)
from api_service.cases.run_operations import cancel_active_case_run, start_neuroimaging_run
from api_service.deps import get_context, get_db
from api_service.helpers import get_workspace_for_user
from api_service.jobs import job_manager
from api_service.policies import require_workspace_manage
from api_service.runtime import settings
from api_service.schemas import (
    ArtifactSummary,
    CaseDetail,
    CaseSummary,
    CaseUpdateRequest,
    CaseUpdateResponse,
    LinkedCaseCreateRequest,
    RunSummary,
    StartRunRequest,
    UploadResponse,
)
from backend_common.auth import AuthContext

router = APIRouter(prefix="/api/app", tags=["cases"])


@router.post("/cases/linked", response_model=UploadResponse)
def create_linked_case(
    request: LinkedCaseCreateRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Create a case backed by a native, read-only host directory."""
    return create_case_from_linked_source(db, context, request=request)


@router.get("/cases", response_model=list[CaseSummary])
def list_cases(
    workspace_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[CaseSummary]:
    """List active cases visible to the current user, optionally scoped to a workspace."""
    return list_visible_cases(db, context, workspace_id=workspace_id)


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case_detail(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> CaseDetail:
    """Return case metadata, artifacts, runs, and assistant thread details."""
    return get_case_detail_for_user(db, context, case_id=case_id)


async def create_case_with_upload(
    workspace_id: str = Form(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    modalities: str | None = Form(None),
    tags: str | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    relative_paths: str | None = Form(None),
    import_mode: str = Form("auto"),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Create a case from uploaded input files."""
    if not isinstance(relative_paths, str):
        relative_paths = None
    if not isinstance(import_mode, str):
        import_mode = "auto"
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
        relative_paths=relative_paths,
        import_mode=import_mode,
    )


async def add_case_upload(
    case_id: str,
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    relative_paths: str | None = Form(None),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Attach uploaded input files to an existing case."""
    if not isinstance(relative_paths, str):
        relative_paths = None
    return await add_upload_to_case(db, context, case_id=case_id, file=file, files=files, relative_paths=relative_paths)


def _multipart_text(form: Any, key: str) -> str | None:
    value = form.get(key)
    return value if isinstance(value, str) else None


def _multipart_uploads(form: Any, key: str) -> list[UploadFile]:
    return [value for value in form.getlist(key) if hasattr(value, "filename") and hasattr(value, "file")]


async def _configured_upload_form(request: Request) -> Any:
    # One JSON string carries every relative path. Allow the maximum UTF-8 size
    # implied by the per-path validation while keeping the field itself bounded.
    manifest_part_limit = max(1024 * 1024, settings.max_upload_files * 4096 + 2)
    try:
        return await request.form(max_files=settings.max_upload_files, max_part_size=manifest_part_limit)
    except StarletteHTTPException as exc:
        if exc.status_code == 400 and "Maximum number of files" in str(exc.detail):
            raise HTTPException(
                status_code=413,
                detail=f"Upload contains more than {settings.max_upload_files} files",
            ) from exc
        raise


@router.post("/cases", response_model=UploadResponse)
async def create_case_with_upload_request(
    request: Request,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Parse case uploads with NeuroCade's configured limit instead of Starlette's 1,000-file default."""
    form = await _configured_upload_form(request)
    try:
        workspace_id = _multipart_text(form, "workspace_id")
        if not workspace_id:
            raise HTTPException(status_code=422, detail="workspace_id is required")
        single = _multipart_uploads(form, "file")
        return await create_case_with_upload(
            workspace_id=workspace_id,
            title=_multipart_text(form, "title"),
            description=_multipart_text(form, "description"),
            modalities=_multipart_text(form, "modalities"),
            tags=_multipart_text(form, "tags"),
            notes=_multipart_text(form, "notes"),
            file=single[0] if single else None,
            files=_multipart_uploads(form, "files"),
            relative_paths=_multipart_text(form, "relative_paths"),
            import_mode=_multipart_text(form, "import_mode") or "auto",
            db=db,
            context=context,
        )
    finally:
        await form.close()


@router.post("/cases/{case_id}/uploads", response_model=UploadResponse)
async def add_case_upload_request(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> UploadResponse:
    """Parse additional case files with the same configured upload limit."""
    form = await _configured_upload_form(request)
    try:
        single = _multipart_uploads(form, "file")
        return await add_case_upload(
            case_id=case_id,
            file=single[0] if single else None,
            files=_multipart_uploads(form, "files"),
            relative_paths=_multipart_text(form, "relative_paths"),
            db=db,
            context=context,
        )
    finally:
        await form.close()


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
    """Create and submit a configured neuroimaging workflow for a case."""
    return await start_neuroimaging_run(db, context, request=request)


@router.get("/cases/{case_id}/runs", response_model=list[RunSummary])
def case_runs(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[RunSummary]:
    """List durable runs for a case."""
    return list_case_runs_for_user(db, context, case_id=case_id)


@router.get("/cases/{case_id}/logs", response_model=dict)
def case_logs(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Return run-specific logs for the latest case run."""
    return get_case_logs_for_user(db, context, case_id=case_id)


@router.get("/queue-status", response_model=dict)
def queue_status(
    workspace_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Return runtime queue status for workspace managers."""
    _workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_manage(role, detail="Only owners/admins can inspect global queue status")
    return job_manager.queue_status()


@router.post("/cases/{case_id}/cancel", response_model=dict)
def cancel_case_run(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Cancel the active runtime job for a case and mark its run canceled."""
    return cancel_active_case_run(db, context, case_id=case_id)


@router.patch("/cases/{case_id}", response_model=CaseUpdateResponse)
def update_case(
    case_id: str,
    request: CaseUpdateRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> CaseUpdateResponse:
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
