"""Case API operations that coordinate storage, DB state, and runtime work."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.artifacts.service import filter_existing_artifacts
from api_service.cases.identity import move_path_or_raise, rewrite_case_references, rollback_path_move
from api_service.cases.serializers import serialize_case_detail, serialize_case_summary, serialize_run_summary
from api_service.cases.service import (
    build_case_slug,
    ensure_case_not_active,
    ensure_case_title_available,
    latest_case_run,
    lock_case_for_run,
    normalize_metadata_list,
    normalize_optional_text,
    raise_case_conflict,
    render_case_logs,
    require_mutations_enabled,
    require_uploads_enabled,
    sync_analysis_run_status,
    validate_case_name_or_400,
)
from api_service.cases.uploads import (
    _artifact_disk_path,
    _collect_upload_files,
    _container_path,
    _copy_input_artifact_to_case,
    _require_input_volume_artifact,
    _store_uploaded_inputs,
    _upload_filename,
)
from api_service.helpers import (
    ensure_case_storage_synced,
    get_case_for_user,
    get_workspace_for_user,
    log_event,
)
from api_service.policies import require_case_manage, require_case_read, require_case_write, require_workspace_write
from api_service.runtime import settings
from api_service.runtime.service import runtime_service
from api_service.schemas import RunSummary, CaseDetail, CaseRenameRequest, CaseRenameResponse, CaseSummary, StartRunRequest, UploadResponse
from backend_common.auth import AuthContext
from backend_common.case_events import record_case_event
from backend_common.case_storage import (
    case_relative_prefix,
    case_storage_dir,
    case_title_from_filename,
    delete_case_storage,
    ensure_case_storage_layout,
)
from backend_common.concurrency import lock_case_for_update, lock_workspace_for_update
from backend_common.db import (
    Artifact,
    AssistantCheckpoint,
    AssistantMessage,
    AssistantScope,
    AssistantThread,
    AuditEvent,
    Case,
    CaseEvent,
    Run,
    RunStatus,
    Workspace,
    WorkspaceMembership,
)
from backend_common.run_statuses import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES


async def list_visible_cases(
    db: Session,
    context: AuthContext,
    *,
    workspace_id: str | None = None,
) -> list[CaseSummary]:
    """List active cases visible to the current user, optionally scoped to a workspace."""
    if workspace_id:
        get_workspace_for_user(db, workspace_id, context.user.id)
    query = (
        db.query(Case, WorkspaceMembership.role)
        .join(Workspace, Workspace.id == Case.workspace_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == Case.workspace_id) & (WorkspaceMembership.user_id == context.user.id),
        )
        .filter(
            Workspace.status == "active",
        )
    )
    if workspace_id:
        query = query.filter(Case.workspace_id == workspace_id)

    summaries: list[CaseSummary] = []
    for case, role in query.all():
        thread = (
            db.query(AssistantThread)
            .filter(
                AssistantThread.scope_type == AssistantScope.case,
                AssistantThread.case_id == case.id,
            )
            .one_or_none()
        )
        latest_run = await sync_analysis_run_status(case, latest_case_run(db, case.id), db)
        case_artifacts = db.query(Artifact).filter(Artifact.case_id == case.id).all()
        summaries.append(
            serialize_case_summary(
                case,
                role,
                thread=thread,
                latest_run=latest_run,
                artifact_count=len(filter_existing_artifacts(case_artifacts)),
            )
        )
    log_event(db, context, "case.list")
    return summaries


def get_case_detail_for_user(db: Session, context: AuthContext, *, case_id: str) -> CaseDetail:
    """Return case metadata, artifacts, runs, and assistant thread details."""
    case, role = get_case_for_user(db, case_id, context.user.id)
    thread = (
        db.query(AssistantThread)
        .filter(
            AssistantThread.scope_type == AssistantScope.case,
            AssistantThread.case_id == case.id,
        )
        .one_or_none()
    )
    artifacts = db.query(Artifact).filter(Artifact.case_id == case.id).order_by(Artifact.created_at.desc()).all()
    runs = db.query(Run).filter(Run.case_id == case.id).order_by(Run.created_at.desc()).all()
    log_event(db, context, "case.viewed", case_id=case_id)
    return serialize_case_detail(
        case,
        role,
        thread=thread,
        artifacts=filter_existing_artifacts(artifacts),
        runs=runs,
    )


async def create_case_from_upload(
    db: Session,
    context: AuthContext,
    *,
    workspace_id: str,
    title: str | None,
    description: str | None,
    modalities: str | None,
    tags: str | None,
    notes: str | None,
    file: UploadFile | None,
    files: list[UploadFile] | None,
) -> UploadResponse:
    """Create a case from uploaded input files."""
    require_uploads_enabled()
    workspace, workspace_role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_write(workspace_role)
    workspace = lock_workspace_for_update(db, workspace)

    upload_files = _collect_upload_files(file, files)
    original_filename = _upload_filename(upload_files[0])
    case_title = validate_case_name_or_400((title or "").strip() or case_title_from_filename(original_filename))
    ensure_case_title_available(db, workspace.id, case_title)

    case = Case(
        id=build_case_slug(workspace.id, case_title),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title=case_title,
        description=normalize_optional_text(description),
        modalities_json=normalize_metadata_list(modalities),
        tags_json=normalize_metadata_list(tags),
        notes=normalize_optional_text(notes),
    )
    db.add(case)
    try:
        db.flush()
        ensure_case_storage_layout(db, settings, case, workspace)
        artifact = await _store_uploaded_inputs(db, case, workspace, upload_files, use_canonical_name=True)
        record_case_event(
            db,
            case,
            "case.uploaded",
            user_id=context.user.id,
            artifact_id=artifact.id,
            details={
                "filename": artifact.name,
                "source_filename": original_filename,
                "upload_count": len(upload_files),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        delete_case_storage(settings, case, workspace)
        raise_case_conflict(exc, f"Case '{case_title}' already exists in this workspace")
    except Exception:
        db.rollback()
        delete_case_storage(settings, case, workspace)
        raise
    log_event(db, context, "artifact.uploaded", case_id=case.id, details={"filename": original_filename})
    return UploadResponse(case_id=case.id, workspace_id=workspace.id, filename=artifact.name, title=case.title)


async def add_upload_to_case(
    db: Session,
    context: AuthContext,
    *,
    case_id: str,
    file: UploadFile | None,
    files: list[UploadFile] | None,
) -> UploadResponse:
    """Attach uploaded input files to an existing case."""
    require_uploads_enabled()
    case, role = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    case = lock_case_for_update(db, case)
    workspace = ensure_case_storage_synced(db, case)
    ensure_case_not_active(db, case)

    upload_files = _collect_upload_files(file, files)
    original_filename = _upload_filename(upload_files[0])
    try:
        artifact = await _store_uploaded_inputs(db, case, workspace, upload_files, use_canonical_name=False)
        record_case_event(
            db,
            case,
            "case.uploaded",
            user_id=context.user.id,
            artifact_id=artifact.id,
            details={
                "filename": artifact.name,
                "source_filename": original_filename,
                "upload_count": len(upload_files),
                "added_to_case": True,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "artifact" in locals():
            _artifact_disk_path(artifact.relative_path).unlink(missing_ok=True)
        raise_case_conflict(exc, "Case upload conflicts with another update. Please retry.")
    log_event(db, context, "artifact.uploaded", case_id=case.id, details={"filename": original_filename})
    return UploadResponse(case_id=case.id, workspace_id=workspace.id, filename=artifact.name, title=case.title)


async def start_fastsurfer_run(db: Session, context: AuthContext, *, request: StartRunRequest) -> RunSummary:
    """Create and submit a FastSurfer run for a case."""
    require_mutations_enabled()
    created_new_case = False

    if request.case_id:
        case, role = get_case_for_user(db, request.case_id, context.user.id)
        require_case_write(role, detail="Case not found")
        case = lock_case_for_run(db, case)
        if request.workspace_id and case.workspace_id != request.workspace_id:
            raise HTTPException(status_code=400, detail="Case does not belong to the requested workspace")
        workspace = ensure_case_storage_synced(db, case)
        ensure_case_not_active(db, case)
    elif request.source_case_id and request.case_name and request.workspace_id:
        source_case, role = get_case_for_user(db, request.source_case_id, context.user.id)
        require_case_read(role)
        source_input = _require_input_volume_artifact(db, source_case, request.input_artifact_id)
        workspace, workspace_role = get_workspace_for_user(db, request.workspace_id, context.user.id)
        require_workspace_write(workspace_role)
        workspace = lock_workspace_for_update(db, workspace)
        source_case = lock_case_for_update(db, source_case)
        case_title = validate_case_name_or_400(request.case_name)
        ensure_case_title_available(db, workspace.id, case_title)
        case = Case(
            id=build_case_slug(workspace.id, case_title),
            workspace_id=workspace.id,
            owner_user_id=context.user.id,
            title=case_title,
        )
        db.add(case)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise_case_conflict(exc, f"Case '{case_title}' already exists in this workspace")
        ensure_case_storage_layout(db, settings, case, workspace)
        input_artifact = _copy_input_artifact_to_case(db, source_input, case, workspace)
        created_new_case = True
    else:
        raise HTTPException(status_code=400, detail="Either case_id or source_case_id + case_name + workspace_id must be provided")

    if not created_new_case:
        input_artifact = _require_input_volume_artifact(db, case, request.input_artifact_id)
    run = Run(
        case_id=case.id,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        status=RunStatus.queued,
        run_type="run_fastsurfer",
        input_json={"input_artifact_id": input_artifact.id},
        runtime_job_id=case.id,
        result_json={"status": "queued"},
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if created_new_case:
            delete_case_storage(settings, case, workspace)
        raise_case_conflict(exc, "Case already has an active run")
    db.refresh(run)

    payload = {
        "workspace_id": workspace.id,
        "user_id": case.owner_user_id,
        "case_id": case.id,
        "subject_name": case.title,
        "input_path": _container_path(_artifact_disk_path(input_artifact.relative_path)),
        "input_artifact_id": input_artifact.id,
        "seg_only": str(request.seg_only).lower(),
        "surf_only": str(request.surf_only).lower(),
        "no_bias": str(request.no_bias).lower(),
        "no_cereb": str(request.no_cereb).lower(),
        "no_asegdkt": str(request.no_asegdkt).lower(),
        "no_hypothal": str(request.no_hypothal).lower(),
        "three_t": str(request.three_t).lower(),
        "vox_size": request.vox_size,
    }
    try:
        response = await runtime_service.start_run(payload)
    except Exception as exc:
        if created_new_case:
            delete_case_storage(settings, case, workspace)
            db.flush()
            db.query(CaseEvent).filter(CaseEvent.case_id == case.id).delete(synchronize_session=False)
            db.query(Artifact).filter(Artifact.case_id == case.id).delete(synchronize_session=False)
            db.query(Run).filter(Run.case_id == case.id).delete(synchronize_session=False)
            db.delete(case)
        else:
            run.status = RunStatus.failed
            run.error_message = str(exc)
        db.commit()
        raise

    run.runtime_job_id = response.get("case_id") or case.id
    run.external_task_id = response.get("task_id")
    run.result_json = response
    db.commit()
    db.refresh(run)
    log_event(db, context, "run.started", case_id=case.id, details={"run_id": run.id})
    return serialize_run_summary(run)


async def list_case_runs_for_user(db: Session, context: AuthContext, *, case_id: str) -> list[RunSummary]:
    """List runs for a case, syncing the latest runtime status first."""
    case, role = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    runs = db.query(Run).filter(Run.case_id == case_id).order_by(Run.created_at.desc()).all()
    if runs:
        latest_run = await sync_analysis_run_status(case, runs[0], db)
        if latest_run is not None:
            runs[0] = latest_run
        if latest_run is not None and latest_run.status not in ACTIVE_RUN_STATUSES:
            ensure_case_storage_synced(db, case)
    else:
        ensure_case_storage_synced(db, case)
    return [serialize_run_summary(run) for run in runs]


async def get_case_logs_for_user(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Return live runtime logs or stored logs for the latest case run."""
    case, role = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    latest_run = await sync_analysis_run_status(case, latest_case_run(db, case.id), db)
    if latest_run is not None and latest_run.status in ACTIVE_RUN_STATUSES:
        return await runtime_service.fetch_logs(case.id, case.workspace_id)

    workspace = ensure_case_storage_synced(db, case)
    return {"logs": render_case_logs(case, workspace)}


async def cancel_active_case_run(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Cancel the active runtime job for a case and mark its run canceled."""
    require_mutations_enabled()
    case, role = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    case = lock_case_for_update(db, case)
    await runtime_service.cancel(case.id, case.workspace_id)
    latest_run = latest_case_run(db, case_id)
    if latest_run is not None and latest_run.status not in TERMINAL_RUN_STATUSES:
        latest_run.status = RunStatus.canceled
        latest_run.error_message = None
        db.commit()
    log_event(db, context, "run.canceled", case_id=case_id)
    return {"status": "canceled", "case_id": case_id}


def update_case_metadata(
    db: Session,
    context: AuthContext,
    *,
    case_id: str,
    request: CaseRenameRequest,
) -> CaseRenameResponse:
    """Update a case title and editable metadata fields."""
    require_mutations_enabled()
    case, role = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Insufficient permission to rename case")
    case = lock_case_for_update(db, case)
    workspace = db.get(Workspace, case.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    ensure_case_not_active(db, case)
    old_title = case.title
    old_case_id = case.id
    title_changed = False
    moved_case_storage = False
    moved_case_source: Path | None = None
    moved_case_target: Path | None = None
    requested_title = getattr(request, "title", None)
    requested_description = getattr(request, "description", None)
    requested_modalities = getattr(request, "modalities", None)
    requested_tags = getattr(request, "tags", None)
    requested_notes = getattr(request, "notes", None)
    if requested_title is not None:
        new_title = validate_case_name_or_400(requested_title)
        ensure_case_title_available(db, workspace.id, new_title, exclude_case_id=case.id)
        title_changed = new_title != case.title
        if title_changed:
            new_case_id = build_case_slug(workspace.id, new_title)
            if db.get(Case, new_case_id) is not None:
                raise HTTPException(status_code=409, detail=f"Case id '{new_case_id}' already exists")
            old_case_dir = case_storage_dir(settings, workspace.id, old_case_id)
            new_case_dir = case_storage_dir(settings, workspace.id, new_case_id)
            moved_case_source = old_case_dir
            moved_case_target = new_case_dir
            old_case_prefix = case_relative_prefix(workspace.id, old_case_id)
            new_case_prefix = case_relative_prefix(workspace.id, new_case_id)
            moved_case_storage = move_path_or_raise(old_case_dir, new_case_dir)
            try:
                case.id = new_case_id
                case.title = new_title
                db.flush()
                rewrite_case_references(
                    db,
                    old_workspace_id=workspace.id,
                    new_workspace_id=workspace.id,
                    old_case_id=old_case_id,
                    new_case_id=new_case_id,
                    old_case_prefix=old_case_prefix,
                    new_case_prefix=new_case_prefix,
                )
            except Exception:
                rollback_path_move(old_case_dir, new_case_dir, moved_case_storage)
                raise
        else:
            case.title = new_title
    if requested_description is not None:
        case.description = normalize_optional_text(requested_description)
    if requested_modalities is not None:
        case.modalities_json = normalize_metadata_list(requested_modalities)
    if requested_tags is not None:
        case.tags_json = normalize_metadata_list(requested_tags)
    if requested_notes is not None:
        case.notes = normalize_optional_text(requested_notes)
    if (
        requested_title is None
        and requested_description is None
        and requested_modalities is None
        and requested_tags is None
        and requested_notes is None
    ):
        raise HTTPException(status_code=400, detail="No case updates requested")
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if moved_case_target and moved_case_source:
            rollback_path_move(moved_case_source, moved_case_target, moved_case_storage)
        raise_case_conflict(exc, f"Case '{case.title}' already exists in this workspace")
    try:
        if title_changed:
            ensure_case_storage_layout(db, settings, case, workspace)
    except Exception:
        db.rollback()
        if moved_case_target and moved_case_source:
            rollback_path_move(moved_case_source, moved_case_target, moved_case_storage)
        raise
    try:
        db.commit()
    except Exception:
        db.rollback()
        if moved_case_target and moved_case_source:
            rollback_path_move(moved_case_source, moved_case_target, moved_case_storage)
        raise
    log_event(db, context, "case.updated", case_id=case.id, details={"old_id": old_case_id, "new_id": case.id, "old_title": old_title, "new_title": case.title})
    response = CaseRenameResponse(
        old_id=case_id,
        new_id=case.id,
        title=case.title,
        case_id=case.id,
        old_title=old_title,
        new_title=case.title,
    )
    if (
        requested_description is not None
        or requested_modalities is not None
        or requested_tags is not None
        or requested_notes is not None
    ):
        response.description = case.description
        response.modalities = normalize_metadata_list(case.modalities_json)
        response.tags = normalize_metadata_list(case.tags_json)
        response.notes = case.notes
    return response


def purge_case_rows_and_storage(db: Session, case: Case, workspace: Workspace) -> str:
    """Delete a case, its dependent rows, and its on-disk storage."""
    deleted_case_id = case.id
    artifact_ids = [artifact_id for (artifact_id,) in db.query(Artifact.id).filter(Artifact.case_id == deleted_case_id).all()]
    thread_ids = [thread_id for (thread_id,) in db.query(AssistantThread.id).filter(AssistantThread.case_id == deleted_case_id).all()]

    ensure_case_not_active(db, case)
    delete_case_storage(settings, case, workspace)
    db.flush()

    if artifact_ids:
        db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
        db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(CaseEvent).filter(CaseEvent.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(AssistantMessage).filter(AssistantMessage.case_id == deleted_case_id).delete(synchronize_session=False)
    if thread_ids:
        db.query(AssistantMessage).filter(AssistantMessage.thread_id.in_(thread_ids)).delete(synchronize_session=False)
        db.query(AssistantCheckpoint).filter(AssistantCheckpoint.thread_id.in_(thread_ids)).delete(synchronize_session=False)
    db.query(Artifact).filter(Artifact.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(Run).filter(Run.case_id == deleted_case_id).delete(synchronize_session=False)
    if thread_ids:
        db.query(AssistantThread).filter(AssistantThread.id.in_(thread_ids)).delete(synchronize_session=False)
    db.delete(case)
    return deleted_case_id


def delete_case_for_user(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Delete a case and all of its stored data."""
    require_mutations_enabled()
    case, role = get_case_for_user(db, case_id, context.user.id)
    require_case_manage(role, detail="Only owners/admins can delete cases")
    case = lock_case_for_update(db, case)
    workspace = db.get(Workspace, case.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    deleted_case_id = purge_case_rows_and_storage(db, case, workspace)
    db.commit()
    log_event(db, context, "case.deleted", details={"deleted_case_id": deleted_case_id})
    return {"deleted": deleted_case_id}
