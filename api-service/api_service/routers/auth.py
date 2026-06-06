"""Provide API service auth behavior for NeuroCade."""

from fastapi import APIRouter, Depends

from api_service.deps import get_context, get_db
from api_service.helpers import log_event
from api_service.monitoring.security import is_monitoring_admin
from api_service.runtime import settings
from api_service.schemas import SessionBootstrap, UserSummary
from backend_common.deployment_policy import get_deployment_policy
from backend_common.auth import AuthContext
from backend_common.db import Case, Workspace, WorkspaceMembership
from sqlalchemy import func
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/app", tags=["auth"])


@router.get("/session", response_model=SessionBootstrap)
def session_bootstrap(
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> SessionBootstrap:
    """Return the authenticated user's session, workspace, and feature bootstrap data."""
    policy = get_deployment_policy(settings)
    log_event(db, context, "session.bootstrap")
    memberships = (
        db.query(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(WorkspaceMembership.user_id == context.user.id, Workspace.status == "active")
        .order_by(Workspace.is_default.desc(), Workspace.created_at.asc())
        .all()
    )
    workspace_ids = [workspace.id for _, workspace in memberships]
    case_count_rows = (
        db.query(Case.workspace_id, func.count(Case.id))
        .filter(Case.workspace_id.in_(workspace_ids))
        .group_by(Case.workspace_id)
        .all()
        if workspace_ids
        else []
    )
    case_counts = {workspace_id: int(count) for workspace_id, count in case_count_rows}
    workspaces = [
        {
            "id": workspace.id,
            "name": workspace.name,
            "description": workspace.description,
            "role": membership.role.value,
            "kind": workspace.kind,
            "is_default": workspace.is_default,
            "status": workspace.status,
            "case_count": case_counts.get(workspace.id, 0),
        }
        for membership, workspace in memberships
    ]
    if policy.profile == "demo":
        default_workspace_id = next((workspace["id"] for workspace in workspaces if workspace["kind"] == "sample"), None)
    else:
        default_workspace_id = next((workspace["id"] for workspace in workspaces if workspace["is_default"]), None)
    return SessionBootstrap(
        user=UserSummary(id=context.user.id, email=context.user.email, full_name=context.user.full_name),
        role=context.role.value,
        auth_mode=context.auth_mode,
        deployment_profile=policy.profile,
        public_url=policy.public_url,
        features=policy.feature_flags(
            clerk_configured=bool(settings.clerk_publishable_key),
            monitoring_admin=is_monitoring_admin(context),
        ),
        limits=policy.limits(settings),
        sample_data=policy.sample_data(),
        workspaces=workspaces,
        default_workspace_id=default_workspace_id,
        active_workspace_id=default_workspace_id,
    )


@router.get("/me", response_model=dict)
def current_user(context: AuthContext = Depends(get_context)) -> dict:
    """Return the authenticated user's public profile fields."""
    return {"id": context.user.id, "email": context.user.email, "full_name": context.user.full_name}
