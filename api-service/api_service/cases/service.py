"""Case domain helpers used by case routes."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.runtime.service import runtime_service
from backend_common.case_storage import build_case_id, case_storage_dir, validate_case_title
from backend_common.concurrency import lock_case_for_update
from backend_common.deployment_policy import get_deployment_policy
from backend_common.db import AssistantScope, Run, Case, Workspace
from backend_common.runs import WORKSPACE_RUN_ACTIONS
from backend_common.run_statuses import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES, normalize_run_status


async def sync_analysis_run_status(case: Case, run: Run | None, db: Session) -> Run | None:
    """Refresh an active run from the runtime service and persist status changes."""
    if run is None:
        return run
    db.refresh(run)
    if run.status in TERMINAL_RUN_STATUSES or run.status not in ACTIVE_RUN_STATUSES:
        return run

    try:
        remote_status = await runtime_service.fetch_status(case.id, case.workspace_id)
    except Exception:
        return run
    normalized_status = normalize_run_status(remote_status.get("status"))
    if normalized_status is None:
        return run

    db.refresh(run)
    if run.status in TERMINAL_RUN_STATUSES or run.status == normalized_status:
        return run

    error_message = remote_status.get("error")
    updated = (
        db.query(Run)
        .filter(Run.id == run.id, Run.status.notin_(TERMINAL_RUN_STATUSES))
        .update(
            {
                Run.status: normalized_status,
                Run.error_message: str(error_message) if error_message else None,
            },
            synchronize_session=False,
        )
    )
    if not updated:
        db.rollback()
        db.refresh(run)
        return run
    db.commit()
    db.refresh(run)
    db.refresh(run)
    return run


def build_case_slug(workspace_id: str, case_title: str) -> str:
    """Build the globally unique case identifier for a workspace-local slug."""
    return build_case_id(workspace_id, case_title)


def validate_case_name_or_400(name: str) -> str:
    """Validate a case title or raise a 400 response with the validation error."""
    try:
        return validate_case_title(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def ensure_case_title_available(db: Session, workspace_id: str, title: str, *, exclude_case_id: str | None = None) -> None:
    """Reject duplicate case titles within a workspace."""
    query = db.query(Case).filter(
        Case.workspace_id == workspace_id,
        Case.title == title,
    )
    if exclude_case_id is not None:
        query = query.filter(Case.id != exclude_case_id)
    if query.first() is not None:
        raise HTTPException(status_code=409, detail=f"Case '{title}' already exists in this workspace")


def normalize_metadata_list(values: list[str] | str | None) -> list[str]:
    """Normalize metadata labels from JSON arrays, comma-delimited text, or lists."""
    if values is None or not isinstance(values, (list, str)):
        return []
    raw_values: Iterable[object]
    if isinstance(values, str):
        text = values.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Metadata lists must be JSON arrays or comma-separated strings") from exc
            raw_values = parsed if isinstance(parsed, list) else []
        else:
            raw_values = text.split(",")
    else:
        raw_values = values

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, str):
            continue
        label = item.strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def normalize_optional_text(value: str | None) -> str | None:
    """Trim optional text fields and collapse blank strings to None."""
    if value is None or not isinstance(value, str):
        return None
    return value.strip() or None


def latest_case_run(db: Session, case_id: str) -> Run | None:
    """Return the newest run recorded for a case."""
    return db.query(Run).filter(Run.case_id == case_id).order_by(Run.created_at.desc(), Run.id.desc()).first()


def lock_case_for_run(db: Session, case: Case) -> Case:
    """Lock a case row before enqueueing or mutating an run."""
    return lock_case_for_update(db, case)


def raise_case_conflict(exc: IntegrityError, detail: str) -> None:
    """Raise a 409 response chained from an integrity conflict."""
    raise HTTPException(status_code=409, detail=detail) from exc


def ensure_case_not_active(db: Session, case: Case) -> None:
    """Reject case changes while an run is active."""
    active_run = (
        db.query(Run)
        .filter(Run.case_id == case.id, Run.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(Run.created_at.desc(), Run.id.desc())
        .first()
    )
    if active_run is not None:
        raise HTTPException(status_code=409, detail="Cannot modify a case that is currently running")
    active_workspace_runs = (
        db.query(Run)
        .filter(
            Run.workspace_id == case.workspace_id,
            Run.case_id.is_(None),
            Run.scope_type == AssistantScope.workspace,
            Run.run_type.in_(WORKSPACE_RUN_ACTIONS),
            Run.status.in_(ACTIVE_RUN_STATUSES),
        )
        .all()
    )
    for run in active_workspace_runs:
        selected_case_ids = (run.result_json or {}).get("case_ids")
        if not isinstance(selected_case_ids, list) or case.id in selected_case_ids:
            raise HTTPException(status_code=409, detail="Cannot modify a case that is currently running")


def require_uploads_enabled() -> None:
    """Reject upload requests when deployment policy disables uploads."""
    if not get_deployment_policy(settings).uploads_enabled:
        raise HTTPException(status_code=403, detail="Uploads are disabled for this deployment")


def require_mutations_enabled() -> None:
    """Reject mutating requests when deployment policy disables destructive actions."""
    if not get_deployment_policy(settings).destructive_actions_enabled:
        raise HTTPException(status_code=403, detail="This action is disabled for this deployment")


def _read_log_lines(path: Path) -> list[str]:
    """Read a UTF-8 log file, returning newline-terminated lines when available."""
    if not path.exists():
        return []
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line + "\n" for line in raw.split("\n") if line]


def render_case_logs(case: Case, workspace: Workspace) -> str:
    """Combine stored stdout and stderr logs for display, filtering noisy runtime lines."""
    case_dir = case_storage_dir(settings, workspace.id, case.id)
    scripts_dir = case_dir / "scripts"
    combined = _read_log_lines(scripts_dir / "stdout.log")
    if not combined:
        combined = _read_log_lines(case_dir / "stdout.log")
    stderr_lines = _read_log_lines(scripts_dir / "stderr.log")
    if not stderr_lines:
        stderr_lines = _read_log_lines(case_dir / "stderr.log")
    if stderr_lines:
        combined.extend(["--- STDERR ---\n", *stderr_lines])

    filtered_logs: list[str] = []
    for line in combined:
        if "WARNING: Found" in line and "files in subject directory" in line:
            continue
        if "Potentially Overwriting:" in line:
            continue
        filtered_logs.append(line)

    processed_logs: list[str] = []
    for line in filtered_logs:
        if "\r" not in line:
            processed_logs.append(line)
            continue
        segments = line.split("\r")
        last = ""
        for segment in reversed(segments):
            stripped = segment.strip()
            if stripped:
                last = stripped + "\n"
                break
        if last:
            processed_logs.append(last)
    return "".join(processed_logs[-1000:])
