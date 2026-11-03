"""Case API operations that coordinate storage, DB state, and runtime work."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from neurocade_runtime_tools.bridge_client import BridgeClient, BridgeError
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
    reserve_case_file_update,
    validate_case_name_or_400,
)
from api_service.cases.uploads import (
    _collect_upload_files,
    _detect_uploaded_layout,
    _group_multi_subject_uploads,
    _parse_relative_upload_paths,
    _store_uploaded_inputs,
    _suggest_default_layers,
    _upload_filename,
    _uploaded_artifact_metadata,
    _write_upload_file,
)
from api_service.helpers import (
    get_case_for_user,
    get_workspace_for_user,
    log_event,
)
from api_service.policies import require_case_manage, require_case_write, require_workspace_write
from api_service.runtime import settings
from api_service.schemas import CaseUpdateRequest, CaseUpdateResponse, LinkedCaseCreateRequest, UploadResponse
from backend_common.artifact_classification import classify_import_artifact
from backend_common.auth import AuthContext
from backend_common.case_events import record_case_event
from backend_common.case_storage import (
    case_storage_dir,
    case_title_from_filename,
    ensure_case_storage_layout,
    ensure_workspace_storage_layout,
    rename_case_storage,
    slugify_storage_name,
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
    LinkedCaseSource,
    LinkedSourceClaim,
    Run,
)
from backend_common.mcp_lifecycle import ScopeDeletionConflict, reserve_scope_deletion, tombstone_case_invocations
from backend_common.storage import resolve_artifact_path
from backend_common.storage_transactions import (
    finalize_staged_path,
    restore_staged_path,
    stage_deletion_for_transaction,
    stage_path_for_deletion,
)
from backend_common.submission_lock import serialize_submission


def _linked_subject_groups(
    files: list[dict],
    paths: list[PurePosixPath],
    layout: dict[str, object],
) -> dict[str, tuple[str, list[tuple[dict, PurePosixPath]]]]:
    """Return case title -> (source root, manifest entry/case path)."""
    subject_value = layout.get("subject_ids")
    if layout.get("scope") not in {"dataset", "subjects-directory"} or not isinstance(subject_value, list):
        return {}
    groups: dict[str, tuple[str, list[tuple[dict, PurePosixPath]]]] = {}
    folded_titles: set[str] = set()
    for value in subject_value:
        if not isinstance(value, str) or not value:
            continue
        items: list[tuple[dict, PurePosixPath]] = []
        for entry, full_path in zip(files, paths, strict=True):
            if not full_path.parts or full_path.parts[0] != value or len(full_path.parts) == 1:
                continue
            items.append((entry, PurePosixPath(*full_path.parts[1:])))
        if items:
            title = validate_case_name_or_400(slugify_storage_name(value))
            if title.casefold() in folded_titles:
                raise HTTPException(status_code=409, detail="Detected subject IDs resolve to duplicate case names")
            folded_titles.add(title.casefold())
            groups[title] = (value, items)
    return groups


def _claim_linked_source(db: Session, context: AuthContext, workspace_id: str, source_id: str) -> bool:
    """Bind an opaque bridge capability to the first authenticated workspace using it."""
    claim = db.get(LinkedSourceClaim, source_id)
    if claim is not None:
        if claim.revoked or claim.owner_user_id != context.user.id or claim.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Linked directory authorization is unavailable")
        return False
    db.add(
        LinkedSourceClaim(
            source_id=source_id,
            workspace_id=workspace_id,
            owner_user_id=context.user.id,
        )
    )
    db.flush()
    return True


def _batch_upload_response(
    workspace_id: str,
    layout: dict[str, object],
    transfer: str,
    created: list[UploadResponse],
) -> UploadResponse:
    cases = [
        {
            "case_id": item.case_id,
            "title": item.title,
            "filenames": item.filenames,
            "default_layers": item.default_layers,
            "file_count": item.import_summary.get("file_count", len(item.filenames)),
        }
        for item in created
    ]
    counts: dict[str, int] = {}
    for item in created:
        for kind, count in item.import_summary.get("artifact_counts", {}).items():
            if isinstance(kind, str) and isinstance(count, int):
                counts[kind] = counts.get(kind, 0) + count
    first = created[0]
    return UploadResponse(
        case_id=first.case_id,
        workspace_id=workspace_id,
        filenames=[name for item in created for name in item.filenames],
        title=first.title,
        import_summary={
            **layout,
            "transfer": transfer,
            "created_case_count": len(created),
            "file_count": sum(int(item["file_count"]) for item in cases),
            "artifact_counts": counts,
        },
        default_layers=first.default_layers,
        created_cases=cases,
    )


def _create_one_linked_case(
    db: Session,
    context: AuthContext,
    *,
    workspace,
    request: LinkedCaseCreateRequest,
    title: str,
    source_name: str,
    source_relative: str,
    items: list[tuple[dict, PurePosixPath]],
    layout: dict[str, object],
) -> UploadResponse:
    case = Case(
        id=str(uuid4()),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title=title,
        description=normalize_optional_text(request.description),
        modalities_json=normalize_metadata_list(request.modalities),
        tags_json=normalize_metadata_list(request.tags),
        notes=normalize_optional_text(request.notes),
    )
    with reserve_case_file_update(db, case):
        try:
            db.add(case)
            db.flush()
            ensure_case_storage_layout(settings, case, workspace)
            db.add(
                LinkedCaseSource(
                    case_id=case.id,
                    workspace_id=workspace.id,
                    source_id=request.source_id,
                    source_relative=source_relative,
                    display_name=source_name if source_relative == "." else f"{source_name}/{source_relative}",
                    format=str(layout.get("format") or "generic"),
                )
            )
            artifacts: list[Artifact] = []
            for entry, case_relative in items:
                metadata = _uploaded_artifact_metadata(case_relative, layout)
                metadata.update(
                    {
                        "source": "linked",
                        "read_only": True,
                    }
                )
                classification = classify_import_artifact(Path(case_relative.as_posix()), metadata=metadata)
                if classification is None:
                    continue
                artifact = Artifact(
                    case_id=case.id,
                    workspace_id=workspace.id,
                    kind=classification.kind,
                    name=case_relative.name,
                    relative_path=case_relative.as_posix(),
                    mime_type=classification.mime_type,
                    size_bytes=max(0, int(entry.get("size") or 0)),
                    metadata_json=classification.metadata,
                )
                db.add(artifact)
                db.flush()
                artifacts.append(artifact)
            default_layers = _suggest_default_layers(artifacts)
            selected = {str(layer["artifact_id"]) for layer in default_layers}
            for artifact in artifacts:
                if artifact.kind == ArtifactKind.volume:
                    artifact.metadata_json = {**artifact.metadata_json, "visible": artifact.id in selected}
            record_case_event(
                db,
                case,
                "case.linked",
                user_id=context.user.id,
                details={
                    "source_name": source_name,
                    "format": layout.get("format"),
                    "file_count": len(items),
                },
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            _discard_failed_upload(db, case.id, storage_created=True)
            raise_case_conflict(exc, f"Case '{title}' already exists in this workspace")
        except Exception:
            db.rollback()
            _discard_failed_upload(db, case.id, storage_created=True)
            raise
    counts: dict[str, int] = {}
    for artifact in artifacts:
        counts[artifact.kind.value] = counts.get(artifact.kind.value, 0) + 1
    try:
        log_event(db, context, "case.linked", case_id=case.id, details={"source_name": source_name})
    except Exception:
        db.rollback()
        _discard_failed_upload(db, case.id, storage_created=True)
        raise
    return UploadResponse(
        case_id=case.id,
        workspace_id=workspace.id,
        filenames=[case_relative.name for _entry, case_relative in items],
        title=case.title,
        import_summary={
            **layout,
            "file_count": len(items),
            "indexed_file_count": len(artifacts),
            "artifact_counts": counts,
            "transfer": "linked",
        },
        default_layers=default_layers,
    )


@serialize_submission
def create_case_from_linked_source(
    db: Session,
    context: AuthContext,
    *,
    request: LinkedCaseCreateRequest,
) -> UploadResponse:
    """Create one or more managed cases backed by an authorized host directory."""
    require_mutations_enabled()
    workspace, workspace_role = get_workspace_for_user(db, request.workspace_id, context.user.id)
    require_workspace_write(workspace_role)
    created_claim = _claim_linked_source(db, context, workspace.id, request.source_id)
    try:
        manifest = BridgeClient.from_environment().linked_source_manifest(request.source_id)
    except (BridgeError, ValueError) as exc:
        if created_claim:
            db.rollback()
        raise HTTPException(status_code=422, detail=f"Linked directory is unavailable: {exc}") from exc
    entries = manifest.get("entries")
    if not isinstance(entries, list) or manifest.get("truncated"):
        raise HTTPException(status_code=422, detail="Directory is too large to link safely; copy/upload it instead")
    files = [entry for entry in entries if isinstance(entry, dict) and entry.get("type") == "file"]
    if not files:
        raise HTTPException(status_code=400, detail="Selected directory does not contain any files")
    source_name = str(manifest.get("name") or "Linked data")
    raw_paths = [PurePosixPath(str(entry.get("path") or "")) for entry in files]
    if any(not path.parts or path.is_absolute() or ".." in path.parts for path in raw_paths):
        raise HTTPException(status_code=502, detail="Runtime bridge returned an invalid directory manifest")
    layout = _detect_uploaded_layout([PurePosixPath(source_name) / path for path in raw_paths])
    groups = _linked_subject_groups(files, raw_paths, layout) if request.import_mode != "single" else {}
    if request.import_mode == "dataset" and not groups:
        raise HTTPException(status_code=400, detail="The selected folder does not contain a recognized multi-subject dataset")
    if groups:
        titles = list(groups)
        existing_titles = {value.casefold() for (value,) in db.query(Case.title).filter(Case.workspace_id == workspace.id).all()}
        workspace_dir = ensure_workspace_storage_layout(settings, workspace)
        conflicts = sorted(title for title in titles if title.casefold() in existing_titles or (workspace_dir / "cases" / title).exists())
        if conflicts:
            raise HTTPException(status_code=409, detail=f"Cases already exist for detected subjects: {', '.join(conflicts)}")
        created: list[UploadResponse] = []
        try:
            for title, (source_relative, items) in groups.items():
                created.append(_create_one_linked_case(
                    db, context, workspace=workspace, request=request, title=title, source_name=source_name,
                    source_relative=source_relative, items=items,
                    layout={**layout, "scope": "subject", "subject_ids": [source_relative]},
                ))
        except Exception:
            for result in reversed(created):
                try:
                    _discard_failed_upload(db, result.case_id, storage_created=True)
                except Exception:
                    db.rollback()
            raise
        return _batch_upload_response(workspace.id, layout, "linked", created)

    title = validate_case_name_or_400((request.title or "").strip() or source_name)
    items = list(zip(files, raw_paths, strict=True))
    return _create_one_linked_case(
        db, context, workspace=workspace, request=request, title=title, source_name=source_name,
        source_relative=".", items=items, layout=layout,
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
    relative_paths: str | None = None,
    import_mode: str = "auto",
) -> UploadResponse:
    """Create a case from uploaded input files."""
    require_uploads_enabled()
    workspace, workspace_role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_write(workspace_role)

    if import_mode not in {"auto", "single", "dataset"}:
        raise HTTPException(status_code=400, detail="import_mode must be auto, single, or dataset")
    upload_files = _collect_upload_files(file, files)
    parsed_relative_paths = _parse_relative_upload_paths(relative_paths, upload_files)
    if parsed_relative_paths is not None:
        batch_layout = _detect_uploaded_layout(parsed_relative_paths)
        upload_groups = (
            _group_multi_subject_uploads(upload_files, parsed_relative_paths, batch_layout)
            if import_mode != "single"
            else {}
        )
        if import_mode == "dataset" and not upload_groups:
            raise HTTPException(status_code=400, detail="The upload does not contain a recognized multi-subject dataset")
        if upload_groups:
            case_titles = {subject_id: validate_case_name_or_400(slugify_storage_name(subject_id)) for subject_id in upload_groups}
            folded_titles = [value.casefold() for value in case_titles.values()]
            if len(folded_titles) != len(set(folded_titles)):
                raise HTTPException(status_code=409, detail="Detected subject IDs resolve to duplicate case names")
            existing_titles = {value.casefold() for (value,) in db.query(Case.title).filter(Case.workspace_id == workspace.id).all()}
            workspace_dir = ensure_workspace_storage_layout(settings, workspace)
            conflicts = sorted(
                title for title in case_titles.values() if title.casefold() in existing_titles or (workspace_dir / "cases" / title).exists()
            )
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cases already exist for detected subjects: {', '.join(conflicts)}",
                )

            created: list[UploadResponse] = []
            try:
                for subject_id, (subject_files, subject_paths) in upload_groups.items():
                    created.append(
                        await create_case_from_upload(
                            db,
                            context,
                            workspace_id=workspace_id,
                            title=case_titles[subject_id],
                            description=description,
                            modalities=modalities,
                            tags=tags,
                            notes=notes,
                            file=None,
                            files=subject_files,
                            relative_paths=json.dumps([path.as_posix() for path in subject_paths]),
                            import_mode="single",
                        )
                    )
            except Exception:
                for result in reversed(created):
                    try:
                            _discard_failed_upload(db, result.case_id, storage_created=True)
                    except Exception:
                        db.rollback()
                raise

            return _batch_upload_response(workspace.id, batch_layout, "uploaded", created)
    original_filename = _upload_filename(upload_files[0])
    default_title_source = original_filename
    if parsed_relative_paths and all(len(path.parts) > 1 for path in parsed_relative_paths):
        roots = {path.parts[0] for path in parsed_relative_paths}
        if len(roots) == 1:
            default_title_source = next(iter(roots))
    case_title = validate_case_name_or_400((title or "").strip() or case_title_from_filename(default_title_source))

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
    # Reserve the prospective case before publishing it. A workspace-wide run
    # conflicts with this reservation even though the case does not exist yet.
    with reserve_case_file_update(db, case):
        storage_created = False
        try:
            db.add(case)
            db.commit()
            ensure_case_storage_layout(settings, case, workspace)
            storage_created = True
            artifacts, stored_filenames, import_summary, default_layers = await _store_uploaded_inputs(
                db,
                case,
                workspace,
                upload_files,
                name_after_case=True,
                relative_paths=parsed_relative_paths,
            )
            record_case_event(
                db,
                case,
                "case.uploaded",
                user_id=context.user.id,
                artifact_id=artifacts[0].id if len(artifacts) == 1 else None,
                details={
                    "filenames": stored_filenames,
                    "source_filename": original_filename,
                    "upload_count": len(upload_files),
                },
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            _discard_failed_upload(db, case.id, storage_created=storage_created)
            if isinstance(exc, FileExistsError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if isinstance(exc, IntegrityError):
                raise_case_conflict(exc, f"Case '{case_title}' already exists in this workspace")
            raise
        try:
            log_event(db, context, "artifact.uploaded", case_id=case.id, details={"filename": original_filename})
        except Exception:
            db.rollback()
            _discard_failed_upload(db, case.id, storage_created=True)
            raise
        return UploadResponse(
            case_id=case.id,
            workspace_id=workspace.id,
            filenames=stored_filenames,
            title=case.title,
            import_summary=import_summary,
            default_layers=default_layers,
        )


async def add_upload_to_case(
    db: Session,
    context: AuthContext,
    *,
    case_id: str,
    file: UploadFile | None,
    files: list[UploadFile] | None,
    relative_paths: str | None = None,
) -> UploadResponse:
    """Attach uploaded input files to an existing case."""
    require_uploads_enabled()
    case, workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    ensure_case_not_active(db, case)

    with reserve_case_file_update(db, case):
        upload_files = _collect_upload_files(file, files)
        parsed_relative_paths = _parse_relative_upload_paths(relative_paths, upload_files)
        original_filename = _upload_filename(upload_files[0])
        try:
            artifacts, stored_filenames, import_summary, _default_layers = await _store_uploaded_inputs(
                db,
                case,
                workspace,
                upload_files,
                name_after_case=False,
                relative_paths=parsed_relative_paths,
                initialize_viewer=False,
            )
            record_case_event(
                db,
                case,
                "case.uploaded",
                user_id=context.user.id,
                artifact_id=artifacts[0].id if len(artifacts) == 1 else None,
                details={
                    "filenames": stored_filenames,
                    "source_filename": original_filename,
                    "upload_count": len(upload_files),
                    "added_to_case": True,
                },
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            for artifact in locals().get("artifacts", []):
                resolve_artifact_path(artifact).unlink(missing_ok=True)
            raise_case_conflict(exc, "Case upload conflicts with another update. Please retry.")
        log_event(db, context, "artifact.uploaded", case_id=case.id, details={"filename": original_filename})
        return UploadResponse(
            case_id=case.id,
            workspace_id=workspace.id,
            filenames=stored_filenames,
            title=case.title,
            import_summary=import_summary,
            default_layers=[],
        )


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
    stem = filename[: -len(extension)] if extension and filename.lower().endswith(extension.lower()) else Path(filename).stem
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
    with reserve_case_file_update(db, case):
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
        log_event(
            db, context, "artifact.generated_volume_saved", case_id=case.id, artifact_id=artifact.id, details={"filename": artifact_name}
        )
        return serialize_artifact(artifact)


@serialize_submission
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
    ensure_case_not_active(db, case)
    return _purge_case_rows(db, case)


def mark_linked_source_revoked_if_unused(db: Session, case_id: str) -> str | None:
    """Revoke the claim when deleting its final linked case."""
    source = db.query(LinkedCaseSource).filter(LinkedCaseSource.case_id == case_id).one_or_none()
    if source is None:
        return None
    remaining = (
        db.query(LinkedCaseSource)
        .filter(LinkedCaseSource.source_id == source.source_id, LinkedCaseSource.case_id != case_id)
        .count()
    )
    if remaining:
        return None
    claim = db.get(LinkedSourceClaim, source.source_id)
    if claim is not None:
        claim.revoked = True
    return source.source_id


def revoke_linked_source_capabilities(source_ids: set[str]) -> None:
    """Best-effort removal of committed, server-revoked bridge capabilities."""
    if not source_ids:
        return
    try:
        client = BridgeClient.from_environment()
    except BridgeError:
        return
    for source_id in source_ids:
        try:
            client.revoke_linked_source(source_id)
        except BridgeError:
            continue


@serialize_submission
def _discard_failed_upload(db: Session, case_id: str, *, storage_created: bool) -> None:
    """Discard a provisional case while the caller still holds its file reservation."""
    source_to_revoke: str | None = None
    try:
        reserve_scope_deletion(db)
        case = db.get(Case, case_id)
        if case is not None:
            source_to_revoke = mark_linked_source_revoked_if_unused(db, case_id)
            if storage_created:
                stage_deletion_for_transaction(
                    db,
                    case_storage_dir(settings, case.workspace_id, case.id),
                    settings.outputs_dir / ".trash" / "cases",
                )
            _purge_case_rows(db, case)
        db.commit()
    except Exception:
        db.rollback()
        raise
    revoke_linked_source_capabilities({source_to_revoke} if source_to_revoke else set())


def _purge_case_rows(db: Session, case: Case) -> str:
    """Remove dependent rows after the caller has secured exclusive mutation access."""
    deleted_case_id = case.id
    artifact_ids = [artifact_id for (artifact_id,) in db.query(Artifact.id).filter(Artifact.case_id == deleted_case_id).all()]
    thread_ids = [thread_id for (thread_id,) in db.query(AssistantThread.id).filter(AssistantThread.case_id == deleted_case_id).all()]

    tombstone_case_invocations(db, deleted_case_id)
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
    db.query(LinkedCaseSource).filter(LinkedCaseSource.case_id == deleted_case_id).delete(synchronize_session=False)
    db.query(Run).filter(Run.case_id == deleted_case_id).delete(synchronize_session=False)
    if thread_ids:
        db.query(AssistantThread).filter(AssistantThread.id.in_(thread_ids)).delete(synchronize_session=False)
    db.delete(case)
    return deleted_case_id


@serialize_submission
def delete_case_for_user(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Delete a case and all of its stored data."""
    require_mutations_enabled()
    try:
        reserve_scope_deletion(db)
    except ScopeDeletionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    case, workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_manage(role, detail="Only owners/admins can delete cases")
    ensure_case_not_active(db, case)
    staged_storage = stage_path_for_deletion(
        case_storage_dir(settings, workspace.id, case.id),
        settings.outputs_dir / ".trash" / "cases",
    )
    source_to_revoke = mark_linked_source_revoked_if_unused(db, case.id)
    try:
        deleted_case_id = purge_case_rows(db, case)
        db.commit()
    except Exception:
        db.rollback()
        restore_staged_path(staged_storage)
        raise
    finalize_staged_path(staged_storage)
    revoke_linked_source_capabilities({source_to_revoke} if source_to_revoke else set())
    log_event(db, context, "case.deleted", details={"deleted_case_id": deleted_case_id})
    return {"deleted": deleted_case_id}
