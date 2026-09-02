"""Provide API service policies behavior for NeuroCade."""

from __future__ import annotations

from fastapi import HTTPException

from backend_common.db import RoleEnum

READ_ROLES = frozenset({RoleEnum.owner, RoleEnum.admin, RoleEnum.user})
WRITE_ROLES = READ_ROLES
MANAGE_ROLES = frozenset({RoleEnum.owner, RoleEnum.admin})


def _normalize_role(role: RoleEnum | str | None) -> RoleEnum | None:
    if role is None or isinstance(role, RoleEnum):
        return role
    return RoleEnum(role)


def require_case_read(role: RoleEnum | str | None, *, detail: str = "Case not found") -> RoleEnum:
    """Require a case role with read access and return the normalized role."""
    normalized = _normalize_role(role)
    if normalized is None or normalized not in READ_ROLES:
        raise HTTPException(status_code=404, detail=detail)
    return normalized


def require_case_write(
    role: RoleEnum | str | None,
    *,
    detail: str = "Insufficient permission to modify case",
) -> RoleEnum:
    """Require a case role with write access and return the normalized role."""
    normalized = require_case_read(role)
    if normalized not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail=detail)
    return normalized


def require_case_manage(
    role: RoleEnum | str | None,
    *,
    detail: str = "Only owners/admins can manage cases",
) -> RoleEnum:
    """Require a case owner/admin role and return the normalized role."""
    normalized = require_case_read(role)
    if normalized not in MANAGE_ROLES:
        raise HTTPException(status_code=403, detail=detail)
    return normalized


def require_workspace_read(role: RoleEnum | str | None, *, detail: str = "Workspace not found") -> RoleEnum:
    """Require a workspace role with read access and return the normalized role."""
    normalized = _normalize_role(role)
    if normalized is None or normalized not in READ_ROLES:
        raise HTTPException(status_code=404, detail=detail)
    return normalized


def require_workspace_write(
    role: RoleEnum | str | None,
    *,
    detail: str = "Insufficient permission for workspace write access",
) -> RoleEnum:
    """Require a workspace role with write access and return the normalized role."""
    normalized = require_workspace_read(role)
    if normalized not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail=detail)
    return normalized


def require_workspace_manage(
    role: RoleEnum | str | None,
    *,
    detail: str = "Only owners/admins can update workspaces",
) -> RoleEnum:
    """Require a workspace owner/admin role and return the normalized role."""
    normalized = require_workspace_read(role)
    if normalized not in MANAGE_ROLES:
        raise HTTPException(status_code=403, detail=detail)
    return normalized
