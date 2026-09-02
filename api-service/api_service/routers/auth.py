"""Provide API service auth behavior for NeuroCade."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from api_service.deps import get_context, get_db
from api_service.helpers import log_event
from api_service.runtime import settings
from api_service.schemas import FrontendConfig, SessionBootstrap, UserSummary
from backend_common.auth import AuthContext
from backend_common.case_storage import resolve_workspace_storage
from backend_common.db import Case, Workspace, WorkspaceMembership
from backend_common.deployment_policy import get_deployment_policy

router = APIRouter(prefix="/api/app", tags=["auth"])


@router.get("/frontend-config", response_model=FrontendConfig)
def frontend_config(response: Response) -> FrontendConfig:
    """Return the public configuration required before authentication."""
    response.headers["Cache-Control"] = "no-store"
    return FrontendConfig(
        local_auth_enabled=settings.local_auth_enabled,
        clerk_publishable_key=settings.clerk_publishable_key,
        clerk_jwt_template=settings.clerk_jwt_template,
    )


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
        .filter(WorkspaceMembership.user_id == context.user.id)
        .order_by(Workspace.is_default.desc(), Workspace.created_at.asc())
        .all()
    )
    available_memberships = []
    for membership, workspace in memberships:
        try:
            resolve_workspace_storage(settings, workspace)
        except FileNotFoundError:
            continue
        available_memberships.append((membership, workspace))
    memberships = available_memberships
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
        features=policy.feature_flags(),
        workspaces=workspaces,
        default_workspace_id=default_workspace_id,
    )
