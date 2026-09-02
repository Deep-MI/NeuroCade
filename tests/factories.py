"""Shared database seed helpers for route and run tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from backend_common.auth import AuthContext
from backend_common.db import Case, RoleEnum, User, Workspace, WorkspaceMembership


def seed_workspace_context(
    db_session: Any,
    *,
    user_id: str = "user-1",
    user_email: str = "user@example.com",
    user_name: str = "User",
    workspace_id: str = "workspace-1",
    workspace_name: str = "personal-workspace",
    workspace_kind: str = "personal",
    is_default_workspace: bool = True,
    membership_role: RoleEnum = RoleEnum.owner,
    case_specs: Sequence[tuple[str, str]] = (),
) -> tuple[AuthContext, Workspace, list[Case]]:
    """Create one user, workspace, membership, and optional canonical cases."""
    user = User(
        id=user_id,
        external_auth_id=user_id,
        email=user_email,
        full_name=user_name,
    )
    workspace = Workspace(
        id=workspace_id,
        owner_user_id=user.id,
        name=workspace_name,
        kind=workspace_kind,
        is_default=is_default_workspace,
    )
    cases = [
        Case(
            id=case_id,
            workspace_id=workspace.id,
            owner_user_id=user.id,
            title=case_title,
        )
        for case_id, case_title in case_specs
    ]
    db_session.add_all([user, workspace, *cases])
    db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=membership_role,
            granted_by_user_id=user.id,
        )
    )
    db_session.commit()
    context = AuthContext(user=user, role=membership_role, auth_mode="local")
    return context, workspace, cases
