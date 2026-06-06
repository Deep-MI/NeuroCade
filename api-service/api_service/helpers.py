"""Provide API service helpers behavior for NeuroCade."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.auth import AuthContext
from backend_common.case_storage import ensure_case_storage_layout
from backend_common.db import AuditEvent, Case, RoleEnum, Workspace, WorkspaceMembership


def log_event(
    db: Session,
    context: AuthContext,
    action: str,
    case_id: str | None = None,
    artifact_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Persist an audit event for the current user."""
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
    """Return an active workspace and the user's membership role."""
    workspace_membership = (
        db.query(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(
            WorkspaceMembership.user_id == user_id,
            Workspace.id == workspace_id,
            Workspace.status == "active",
        )
        .one_or_none()
    )
    if workspace_membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    membership, workspace = workspace_membership
    return workspace, membership.role


def resolve_case_role_for_user(db: Session, case_id: str, user_id: str) -> tuple[RoleEnum | None, Workspace | None]:
    """Resolve a user's role and active workspace for an existing case."""
    case = db.get(Case, case_id)
    if case is None:
        return None, None

    workspace_membership = (
        db.query(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(
            WorkspaceMembership.user_id == user_id,
            Workspace.id == case.workspace_id,
            Workspace.status == "active",
        )
        .one_or_none()
    )
    if workspace_membership is None:
        return None, None
    membership, workspace = workspace_membership
    return membership.role, workspace


def ensure_case_storage_synced(db: Session, case: Case) -> Workspace:
    """Ensure the case storage layout exists and return its workspace."""
    workspace = db.get(Workspace, case.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    ensure_case_storage_layout(db, settings, case, workspace)
    return workspace


def get_case_for_user(db: Session, case_id: str, user_id: str, workspace_id: str | None = None) -> tuple[Case, RoleEnum]:
    """Return an existing case and the user's role, or raise 404."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    if workspace_id is not None and case.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Case not found")
    role, _workspace = resolve_case_role_for_user(db, case_id, user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case, role
