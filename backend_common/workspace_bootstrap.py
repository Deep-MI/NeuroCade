"""Provide shared backend workspace bootstrap utilities for NeuroCade."""

from __future__ import annotations

from hashlib import blake2b

from sqlalchemy.orm import Session

from backend_common.case_storage import slugify_storage_name
from backend_common.db import RoleEnum, User, Workspace, WorkspaceMembership

DEFAULT_PERSONAL_WORKSPACE_ID = "personal-workspace"


def _user_workspace_slug_base(user: User) -> str:
    """Return a readable user-derived slug base for Clerk workspace IDs."""
    email = (user.email or "").strip()
    email_local = email.split("@", 1)[0] if "@" in email else email
    base = slugify_storage_name(email_local) or slugify_storage_name(user.full_name or "") or "user"
    return base


def _readable_personal_workspace_id_for_user(db: Session, user: User) -> str:
    """Return a readable, stable, globally unique personal workspace slug for a Clerk user."""
    digest = blake2b(user.id.encode("utf-8"), digest_size=4, person=b"wsid").hexdigest()[:6]
    suffix = f"-workspace-{digest}"
    base = _user_workspace_slug_base(user)[: 64 - len(suffix)].rstrip("-") or "user"
    candidate = f"{base}{suffix}"
    index = 2
    while (existing := db.get(Workspace, candidate)) is not None and existing.owner_user_id != user.id:
        suffix = f"-{index}"
        candidate = f"{base[:64 - len(suffix)]}{suffix}"
        index += 1
    return candidate


def ensure_personal_workspace(db: Session, user: User, *, readable_user_slug: bool = False) -> Workspace:
    """Return the user's default personal workspace and owner membership."""
    workspace = (
        db.query(Workspace)
        .filter(Workspace.owner_user_id == user.id, Workspace.is_default.is_(True))
        .one_or_none()
    )
    if workspace is None:
        workspace_id = _readable_personal_workspace_id_for_user(db, user) if readable_user_slug else DEFAULT_PERSONAL_WORKSPACE_ID
        workspace = Workspace(
            id=workspace_id,
            owner_user_id=user.id,
            name=workspace_id,
            kind="personal",
            is_default=True,
            status="active",
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

    return workspace
