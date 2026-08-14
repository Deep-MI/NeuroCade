"""Provide API service helpers behavior for NeuroCade."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.auth import AuthContext
from backend_common.case_storage import resolve_case_storage, resolve_workspace_storage
from backend_common.db import AuditEvent, Case, RoleEnum, Workspace, WorkspaceMembership

AUDIT_RETENTION_CLEANUP_INTERVAL_SECONDS = 60 * 60
_audit_retention_cleanup_lock = threading.Lock()
_next_audit_retention_cleanup_at = 0.0


def _claim_audit_retention_cleanup() -> bool:
    """Claim the process-wide audit cleanup when it is due."""
    global _next_audit_retention_cleanup_at
    now = time.monotonic()
    with _audit_retention_cleanup_lock:
        if now < _next_audit_retention_cleanup_at:
            return False
        _next_audit_retention_cleanup_at = now + AUDIT_RETENTION_CLEANUP_INTERVAL_SECONDS
        return True


def log_event(
    db: Session,
    context: AuthContext,
    action: str,
    case_id: str | None = None,
    artifact_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Persist an audit event for the current user."""
    if _claim_audit_retention_cleanup():
        retention_days = max(settings.monitoring_event_retention_days, 1)
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        db.query(AuditEvent).filter(AuditEvent.created_at < cutoff).delete(synchronize_session=False)
    db.add(
        AuditEvent(
            user_id=context.user.id,
            case_id=case_id,
            artifact_id=artifact_id,
            action=action,
            details_json=details or {},
        )
    )
    db.commit()


def get_workspace_for_user(db: Session, workspace_id: str, user_id: str) -> tuple[Workspace, RoleEnum]:
    """Return an existing workspace and the user's membership role."""
    workspace_membership = (
        db.query(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(
            WorkspaceMembership.user_id == user_id,
            Workspace.id == workspace_id,
        )
        .one_or_none()
    )
    if workspace_membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    membership, workspace = workspace_membership
    try:
        resolve_workspace_storage(settings, workspace)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workspace storage not found") from exc
    return workspace, membership.role


def get_case_for_user(
    db: Session,
    case_id: str,
    user_id: str,
    workspace_id: str | None = None,
) -> tuple[Case, Workspace, RoleEnum, Path]:
    """Return an authorized case with its workspace and storage directory."""
    query = (
        db.query(Case, Workspace, WorkspaceMembership.role)
        .join(Workspace, Workspace.id == Case.workspace_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == Workspace.id) & (WorkspaceMembership.user_id == user_id),
        )
        .filter(Case.id == case_id)
    )
    if workspace_id is not None:
        query = query.filter(Workspace.id == workspace_id)
    row = query.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Case not found")
    case, workspace, role = row
    try:
        directory = resolve_case_storage(settings, case, workspace)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Case storage not found") from exc
    return case, workspace, role, directory
