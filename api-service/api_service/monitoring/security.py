"""Provide API service monitoring security behavior for NeuroCade."""

from __future__ import annotations

from fastapi import HTTPException

from backend_common.auth import AuthContext
from backend_common.settings import get_settings

settings = get_settings()


def monitoring_admin_user_ids() -> set[str]:
    """Return configured monitoring administrator user IDs."""
    configured = {
        item.strip()
        for item in settings.monitoring_admin_user_ids.split(",")
        if item.strip()
    }
    if configured:
        return configured
    if settings.local_auth_enabled:
        return {settings.local_auth_user_id}
    return set()


def is_monitoring_admin(context: AuthContext) -> bool:
    """Check whether the authenticated user can access monitoring."""
    return context.user.id in monitoring_admin_user_ids()


def require_monitoring_admin(context: AuthContext) -> None:
    """Raise 403 when the authenticated user lacks monitoring access."""
    if not is_monitoring_admin(context):
        raise HTTPException(status_code=403, detail="Monitoring dashboard access is restricted to configured administrators")
