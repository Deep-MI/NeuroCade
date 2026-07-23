"""Case and workspace identity rewrite helpers."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, overload

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.case_storage import (
    build_case_id,
    case_relative_prefix,
    workspace_storage_dir,
    workspace_storage_relative_prefix,
)
from backend_common.db import (
    Artifact,
    AssistantCheckpoint,
    AssistantMessage,
    AssistantThread,
    AuditEvent,
    Case,
    CaseEvent,
    Run,
    Workspace,
    WorkspaceMembership,
)


def _replace_text_token(value: str, old: str, new: str) -> str:
    """Replace ID/path tokens without matching inside unrelated words."""
    if "/" in old:
        boundary_chars = r"A-Za-z0-9_./-"
        pattern = rf"(?<![{boundary_chars}]){re.escape(old)}(?=$|/|[^{boundary_chars}])"
    else:
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(old)}(?![A-Za-z0-9_-])"
    return re.sub(pattern, new, value)


@overload
def _replace_string_tokens(value: str, replacements: dict[str, str]) -> str: ...


@overload
def _replace_string_tokens(value: list[Any], replacements: dict[str, str]) -> list[Any]: ...


@overload
def _replace_string_tokens(value: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]: ...


@overload
def _replace_string_tokens(value: object, replacements: dict[str, str]) -> object: ...


def _replace_string_tokens(value: object, replacements: dict[str, str]) -> object:
    """Recursively replace ID tokens and path fragments in JSON-compatible values."""
    if isinstance(value, str):
        updated = value
        for old, new in replacements.items():
            updated = _replace_text_token(updated, old, new)
        return updated
    if isinstance(value, list):
        return [_replace_string_tokens(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_string_tokens(item, replacements) for key, item in value.items()}
    return value


def _replace_json_dict_tokens(value: dict[str, Any] | None, replacements: dict[str, str]) -> dict[str, Any]:
    """Replace tokens in a JSON object while preserving the mapped-column type."""
    return _replace_string_tokens(value or {}, replacements)


def _replace_prefix(value: str, old_prefix: str, new_prefix: str) -> str:
    """Replace a path prefix when the string is at or below that prefix."""
    if value == old_prefix:
        return new_prefix
    if value.startswith(f"{old_prefix}/"):
        return f"{new_prefix}{value[len(old_prefix):]}"
    return value


def rewrite_case_references(
    db: Session,
    *,
    old_workspace_id: str,
    new_workspace_id: str,
    old_case_id: str,
    new_case_id: str,
    old_case_prefix: str,
    new_case_prefix: str,
) -> None:
    """Rewrite non-FK case references after a case/workspace id change."""
    replacements = {
        old_workspace_id: new_workspace_id,
        old_case_id: new_case_id,
        old_case_prefix: new_case_prefix,
    }
    case_ids = {old_case_id, new_case_id}
    workspace_ids = {old_workspace_id, new_workspace_id}
    for artifact in db.query(Artifact).filter(Artifact.case_id.in_(case_ids)).all():
        if artifact.case_id == old_case_id:
            artifact.case_id = new_case_id
        artifact.relative_path = _replace_prefix(artifact.relative_path, old_case_prefix, new_case_prefix)
        artifact.workspace_id = new_workspace_id
        artifact.metadata_json = _replace_json_dict_tokens(artifact.metadata_json, replacements)
    for run in db.query(Run).filter(Run.case_id.in_(case_ids)).all():
        if run.case_id == old_case_id:
            run.case_id = new_case_id
        if run.runtime_job_id == old_case_id:
            run.runtime_job_id = new_case_id
        run.workspace_id = new_workspace_id
        if run.thread_id:
            run.thread_id = str(_replace_string_tokens(run.thread_id, replacements))
        run.input_json = _replace_json_dict_tokens(run.input_json, replacements)
        run.result_json = _replace_json_dict_tokens(run.result_json, replacements)
    for run in db.query(Run).filter(Run.workspace_id.in_(workspace_ids), Run.case_id.is_(None)).all():
        if run.workspace_id == old_workspace_id:
            run.workspace_id = new_workspace_id
        if run.thread_id:
            run.thread_id = str(_replace_string_tokens(run.thread_id, replacements))
        run.input_json = _replace_json_dict_tokens(run.input_json, replacements)
        run.result_json = _replace_json_dict_tokens(run.result_json, replacements)
    affected_thread_ids: set[str] = set()
    for thread in db.query(AssistantThread).filter(AssistantThread.case_id.in_(case_ids)).all():
        affected_thread_ids.add(thread.id)
        if thread.case_id == old_case_id:
            thread.case_id = new_case_id
        if thread.thread_key == f"case:{old_case_id}":
            thread.thread_key = f"case:{new_case_id}"
        thread.workspace_id = new_workspace_id
    for thread in db.query(AssistantThread).filter(AssistantThread.workspace_id.in_(workspace_ids), AssistantThread.case_id.is_(None)).all():
        affected_thread_ids.add(thread.id)
    for message in db.query(AssistantMessage).filter(AssistantMessage.case_id.in_(case_ids)).all():
        if message.case_id == old_case_id:
            message.case_id = new_case_id
        message.workspace_id = new_workspace_id
        message.content_json = _replace_json_dict_tokens(message.content_json, replacements)
        message.metadata_json = _replace_json_dict_tokens(message.metadata_json, replacements)
    for message in db.query(AssistantMessage).filter(AssistantMessage.workspace_id.in_(workspace_ids), AssistantMessage.case_id.is_(None)).all():
        if message.workspace_id == old_workspace_id:
            message.workspace_id = new_workspace_id
        message.content_json = _replace_json_dict_tokens(message.content_json, replacements)
        message.metadata_json = _replace_json_dict_tokens(message.metadata_json, replacements)
    if affected_thread_ids:
        for checkpoint in db.query(AssistantCheckpoint).filter(AssistantCheckpoint.thread_id.in_(affected_thread_ids)).all():
            checkpoint.state_json = _replace_json_dict_tokens(checkpoint.state_json, replacements)
    for event in db.query(CaseEvent).filter(CaseEvent.case_id.in_(case_ids)).all():
        if event.case_id == old_case_id:
            event.case_id = new_case_id
        event.workspace_id = new_workspace_id
        event.details_json = _replace_json_dict_tokens(event.details_json, replacements)
    for audit_event in db.query(AuditEvent).filter(AuditEvent.case_id.in_(case_ids)).all():
        if audit_event.case_id == old_case_id:
            audit_event.case_id = new_case_id
        audit_event.details_json = _replace_json_dict_tokens(audit_event.details_json, replacements)
    for artifact in db.query(Artifact).filter(Artifact.workspace_id.in_(workspace_ids), Artifact.case_id.is_(None)).all():
        if artifact.workspace_id == old_workspace_id:
            artifact.workspace_id = new_workspace_id
        artifact.metadata_json = _replace_json_dict_tokens(artifact.metadata_json, replacements)


def rewrite_workspace_identity(
    db: Session,
    *,
    workspace: Workspace,
    cases: list[Case],
    new_workspace_id: str,
) -> tuple[Path, Path, bool] | None:
    """Rewrite workspace and child case IDs plus storage paths for a workspace rename."""
    old_workspace_id = workspace.id
    if old_workspace_id == new_workspace_id:
        workspace.name = new_workspace_id
        return None
    if db.get(Workspace, new_workspace_id) is not None:
        raise HTTPException(status_code=409, detail=f"Workspace '{new_workspace_id}' already exists")

    old_workspace_dir = workspace_storage_dir(settings, old_workspace_id)
    new_workspace_dir = workspace_storage_dir(settings, new_workspace_id)
    old_workspace_prefix = workspace_storage_relative_prefix(old_workspace_id)
    new_workspace_prefix = workspace_storage_relative_prefix(new_workspace_id)
    case_id_pairs: list[tuple[Case, str, str, str, str]] = []
    for case in cases:
        new_case_id = build_case_id(new_workspace_id, case.title)
        existing_case = db.get(Case, new_case_id)
        if existing_case is not None and existing_case.id != case.id:
            raise HTTPException(status_code=409, detail=f"Case id '{new_case_id}' already exists")
        case_id_pairs.append(
            (
                case,
                case.id,
                new_case_id,
                case_relative_prefix(old_workspace_id, case.id),
                case_relative_prefix(new_workspace_id, new_case_id),
            )
        )

    moved = move_path_or_raise(old_workspace_dir, new_workspace_dir)
    try:
        workspace_replacements = {
            old_workspace_id: new_workspace_id,
            old_workspace_prefix: new_workspace_prefix,
        }
        workspace.id = new_workspace_id
        workspace.name = new_workspace_id
        db.flush()

        workspace_ids = {old_workspace_id, new_workspace_id}
        for artifact in db.query(Artifact).filter(Artifact.workspace_id.in_(workspace_ids), Artifact.case_id.is_(None)).all():
            if artifact.workspace_id == old_workspace_id:
                artifact.workspace_id = new_workspace_id
            artifact.relative_path = _replace_prefix(artifact.relative_path, old_workspace_prefix, new_workspace_prefix)
            artifact.metadata_json = _replace_json_dict_tokens(artifact.metadata_json, workspace_replacements)
        for run in db.query(Run).filter(Run.workspace_id.in_(workspace_ids), Run.case_id.is_(None)).all():
            if run.workspace_id == old_workspace_id:
                run.workspace_id = new_workspace_id
            if run.thread_id:
                run.thread_id = str(_replace_string_tokens(run.thread_id, workspace_replacements))
            run.input_json = _replace_json_dict_tokens(run.input_json, workspace_replacements)
            run.result_json = _replace_json_dict_tokens(run.result_json, workspace_replacements)
        workspace_thread_ids: set[str] = set()
        for thread in db.query(AssistantThread).filter(AssistantThread.workspace_id.in_(workspace_ids), AssistantThread.case_id.is_(None)).all():
            workspace_thread_ids.add(thread.id)
            if thread.thread_key == f"workspace:{old_workspace_id}":
                thread.thread_key = f"workspace:{new_workspace_id}"
            thread.workspace_id = new_workspace_id
        for message in db.query(AssistantMessage).filter(AssistantMessage.workspace_id.in_(workspace_ids), AssistantMessage.case_id.is_(None)).all():
            if message.workspace_id == old_workspace_id:
                message.workspace_id = new_workspace_id
            message.content_json = _replace_json_dict_tokens(message.content_json, workspace_replacements)
            message.metadata_json = _replace_json_dict_tokens(message.metadata_json, workspace_replacements)
        if workspace_thread_ids:
            for checkpoint in db.query(AssistantCheckpoint).filter(AssistantCheckpoint.thread_id.in_(workspace_thread_ids)).all():
                checkpoint.state_json = _replace_json_dict_tokens(checkpoint.state_json, workspace_replacements)

        for case, old_case_id, new_case_id, old_case_prefix, new_case_prefix in case_id_pairs:
            case.workspace_id = new_workspace_id
            case.id = new_case_id
            db.flush()
            rewrite_case_references(
                db,
                old_workspace_id=old_workspace_id,
                new_workspace_id=new_workspace_id,
                old_case_id=old_case_id,
                new_case_id=new_case_id,
                old_case_prefix=old_case_prefix,
                new_case_prefix=new_case_prefix,
            )

        for membership in db.query(WorkspaceMembership).filter(WorkspaceMembership.workspace_id.in_(workspace_ids)).all():
            if membership.workspace_id == old_workspace_id:
                membership.workspace_id = new_workspace_id
        for event in db.query(CaseEvent).filter(CaseEvent.workspace_id.in_(workspace_ids)).all():
            if event.workspace_id == old_workspace_id:
                event.workspace_id = new_workspace_id
            event.details_json = _replace_json_dict_tokens(event.details_json, workspace_replacements)
        return old_workspace_dir, new_workspace_dir, moved
    except Exception:
        rollback_path_move(old_workspace_dir, new_workspace_dir, moved)
        raise


def rollback_path_move(source: Path, target: Path, moved: bool) -> None:
    """Undo a best-effort path move or remove an empty target created for a missing source."""
    if moved:
        if target.exists() and not source.exists():
            shutil.move(str(target), str(source))
        return
    if target.exists() and target.is_dir() and not any(target.iterdir()):
        target.rmdir()


def move_path_or_raise(source: Path, target: Path) -> bool:
    """Move a path if it exists, returning whether a move occurred."""
    if target.exists():
        raise HTTPException(status_code=409, detail=f"Storage path already exists: {target}")
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return True
