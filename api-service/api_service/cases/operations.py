"""Case API operations that coordinate storage, DB state, and runtime work."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.artifacts.service import serialize_artifact
from api_service.cases.service import (
    ensure_case_not_active,
    normalize_metadata_list,
    normalize_optional_text,
    raise_case_conflict,
    require_mutations_enabled,
    require_uploads_enabled,
    validate_case_name_or_400,
)
from api_service.cases.uploads import (
    _collect_upload_files,
    _store_uploaded_inputs,
    _upload_filename,
    _write_upload_file,
)
from api_service.helpers import (
    get_case_for_user,
    get_workspace_for_user,
    log_event,
)
from api_service.policies import require_case_manage, require_case_write, require_workspace_write
from api_service.runtime import settings
from api_service.schemas import CaseUpdateRequest, CaseUpdateResponse, UploadResponse
from backend_common.auth import AuthContext
from backend_common.case_events import record_case_event
from backend_common.case_storage import (
    case_storage_dir,
    case_title_from_filename,
    delete_case_storage,
    ensure_case_storage_layout,
    rename_case_storage,
    upload_extension,
)
from backend_common.db import (
    Artifact,
    ArtifactKind,
    AssistantMessage,
    AssistantThread,
    AssistantTurn,
    AuditEvent,
    Case,
    CaseEvent,
    Run,
)
from backend_common.storage import resolve_artifact_path
from backend_common.storage_transactions import finalize_staged_path, restore_staged_path, stage_path_for_deletion


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

    upload_files = _collect_upload_files(file, files)
    original_filename = _upload_filename(upload_files[0])
    case_title = validate_case_name_or_400((title or "").strip() or case_title_from_filename(original_filename))

    case = Case(
        id=str(uuid4()),
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
        ensure_case_storage_layout(settings, case, workspace)
        artifact = await _store_uploaded_inputs(db, case, workspace, upload_files, name_primary_after_case=True)
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
    except FileExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    case, workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    ensure_case_not_active(db, case)

    upload_files = _collect_upload_files(file, files)
    original_filename = _upload_filename(upload_files[0])
    try:
        artifact = await _store_uploaded_inputs(db, case, workspace, upload_files, name_primary_after_case=False)
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
            resolve_artifact_path(artifact).unlink(missing_ok=True)
        raise_case_conflict(exc, "Case upload conflicts with another update. Please retry.")
    log_event(db, context, "artifact.uploaded", case_id=case.id, details={"filename": original_filename})
    return UploadResponse(case_id=case.id, workspace_id=workspace.id, filename=artifact.name, title=case.title)


def _safe_generated_volume_name(filename: str) -> str:
    clean = Path(filename.strip()).name
    if not clean:
        raise HTTPException(status_code=400, detail="Filename is required")
    lower = clean.lower()
    if not (lower.endswith(".nii") or lower.endswith(".nii.gz") or lower.endswith(".mgz")):
        raise HTTPException(status_code=400, detail="Generated volume filename must end with .nii, .nii.gz, or .mgz")
    if clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise HTTPException(status_code=400, detail="Generated volume filename must not contain path separators")
    return clean


def _unique_generated_volume_name(case_dir: Path, filename: str) -> str:
    extension = upload_extension(filename)
    stem = filename[:-len(extension)] if extension and filename.lower().endswith(extension.lower()) else Path(filename).stem
    candidate = f"{stem}{extension}"
    index = 2
    while (case_dir / candidate).exists():
        candidate = f"{stem}-{index}{extension}"
        index += 1
    return candidate


async def save_generated_case_volume(
    db: Session,
    context: AuthContext,
    *,
    case_id: str,
    filename: str,
    metadata: str | None,
    file: UploadFile,
):
    """Save a generated viewer volume into a case and register it as an artifact."""
    require_mutations_enabled()
    case, workspace, role, case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    requested_name = _safe_generated_volume_name(filename)
    artifact_name = _unique_generated_volume_name(case_dir, requested_name)
    target_path = case_dir / artifact_name
    try:
        metadata_json = json.loads(metadata or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Generated volume metadata must be valid JSON") from exc
    if not isinstance(metadata_json, dict):
        raise HTTPException(status_code=400, detail="Generated volume metadata must be an object")
    lut = metadata_json.get("lut")
    if lut not in {"binary", "freesurfer"}:
        lut = "freesurfer"
    metadata_json["volume_role"] = "segmentation"
    metadata_json["lut"] = lut
    metadata_json.setdefault("layer_role", "drawing")

    size_bytes, mime_type = await _write_upload_file(file, target_path)
    if size_bytes == 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Generated volume file is empty")
    artifact = Artifact(
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name=artifact_name,
        relative_path=artifact_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        metadata_json=metadata_json,
    )
    db.add(artifact)
    try:
        db.flush()
        record_case_event(
            db,
            case,
            "artifact.generated_volume_saved",
            user_id=context.user.id,
            artifact_id=artifact.id,
            details={"filename": artifact_name, "source": metadata_json.get("source_layer_id")},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        target_path.unlink(missing_ok=True)
        raise_case_conflict(exc, "Generated volume conflicts with another saved artifact. Please retry.")
    except Exception:
        db.rollback()
        target_path.unlink(missing_ok=True)
        raise
    db.refresh(artifact)
    log_event(db, context, "artifact.generated_volume_saved", case_id=case.id, artifact_id=artifact.id, details={"filename": artifact_name})
    return serialize_artifact(artifact)


def update_case_metadata(
    db: Session,
    context: AuthContext,
    *,
    case_id: str,
    request: CaseUpdateRequest,
) -> CaseUpdateResponse:
    """Update a case title and editable metadata fields."""
    require_mutations_enabled()
    case, workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Insufficient permission to rename case")
    ensure_case_not_active(db, case)
    old_title = case.title
    if not request.model_fields_set:
        raise HTTPException(status_code=400, detail="No case updates requested")
    if request.title is not None:
        new_title = validate_case_name_or_400(request.title)
        if new_title != case.title:
            try:
                rename_case_storage(settings, workspace.id, case.id, new_title)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        case.title = new_title
    if "description" in request.model_fields_set:
        case.description = normalize_optional_text(request.description)
    if "modalities" in request.model_fields_set:
        case.modalities_json = normalize_metadata_list(request.modalities)
    if "tags" in request.model_fields_set:
        case.tags_json = normalize_metadata_list(request.tags)
    if "notes" in request.model_fields_set:
        case.notes = normalize_optional_text(request.notes)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise_case_conflict(exc, f"Case '{case.title}' already exists in this workspace")
    log_event(db, context, "case.updated", case_id=case.id, details={"old_title": old_title, "new_title": case.title})
    return CaseUpdateResponse(
        id=case.id,
        title=case.title,
        description=case.description,
        modalities=normalize_metadata_list(case.modalities_json),
        tags=normalize_metadata_list(case.tags_json),
        notes=case.notes,
    )


def purge_case_rows(db: Session, case: Case) -> str:
    """Delete a case and its dependent rows inside the current transaction."""
    deleted_case_id = case.id
    artifact_ids = [artifact_id for (artifact_id,) in db.query(Artifact.id).filter(Artifact.case_id == deleted_case_id).all()]
    thread_ids = [thread_id for (thread_id,) in db.query(AssistantThread.id).filter(AssistantThread.case_id == deleted_case_id).all()]

    ensure_case_not_active(db, case)
    db.flush()

    if artifact_ids:
        db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
        db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(CaseEvent).filter(CaseEvent.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(AssistantMessage).filter(AssistantMessage.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(AssistantTurn).filter(AssistantTurn.case_id == deleted_case_id).delete(synchronize_session=False)
    if thread_ids:
        db.query(AssistantMessage).filter(AssistantMessage.thread_id.in_(thread_ids)).delete(synchronize_session=False)
        db.query(AssistantTurn).filter(AssistantTurn.thread_id.in_(thread_ids)).delete(synchronize_session=False)
    db.query(Artifact).filter(Artifact.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(Run).filter(Run.case_id == deleted_case_id).delete(synchronize_session=False)
    if thread_ids:
        db.query(AssistantThread).filter(AssistantThread.id.in_(thread_ids)).delete(synchronize_session=False)
    db.delete(case)
    return deleted_case_id


def delete_case_for_user(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Delete a case and all of its stored data."""
    require_mutations_enabled()
    case, workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_manage(role, detail="Only owners/admins can delete cases")
    staged_storage = stage_path_for_deletion(
        case_storage_dir(settings, workspace.id, case.id),
        settings.outputs_dir / ".trash" / "cases",
    )
    try:
        deleted_case_id = purge_case_rows(db, case)
        db.commit()
    except Exception:
        db.rollback()
        restore_staged_path(staged_storage)
        raise
    finalize_staged_path(staged_storage)
    log_event(db, context, "case.deleted", details={"deleted_case_id": deleted_case_id})
    return {"deleted": deleted_case_id}
