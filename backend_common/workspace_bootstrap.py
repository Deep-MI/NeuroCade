"""Provide shared backend workspace bootstrap utilities for NeuroCade."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from backend_common.case_storage import ensure_workspace_storage_layout
from backend_common.db import RoleEnum, User, Workspace, WorkspaceMembership


def _available_personal_workspace_name(settings) -> str:
    root = settings.outputs_dir / "workspaces"
    name = "personal-workspace"
    index = 2
    while (root / name).exists():
        name = f"personal-workspace-{index}"
        index += 1
    return name


def ensure_personal_workspace(db: Session, settings, user: User) -> Workspace:
    """Return the user's default personal workspace and owner membership."""
    workspace = (
        db.query(Workspace)
        .filter(Workspace.owner_user_id == user.id, Workspace.is_default.is_(True))
        .one_or_none()
    )
    if workspace is None:
        workspace = Workspace(
            id=str(uuid4()),
            owner_user_id=user.id,
            name=_available_personal_workspace_name(settings),
            kind="personal",
            is_default=True,
        )
        db.add(workspace)
        db.flush()

    membership = (
        db.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id, WorkspaceMembership.user_id == user.id)
        .one_or_none()
    )
    if membership is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=RoleEnum.owner,
                granted_by_user_id=user.id,
            )
        )
        db.flush()

    ensure_workspace_storage_layout(settings, workspace)
    return workspace
