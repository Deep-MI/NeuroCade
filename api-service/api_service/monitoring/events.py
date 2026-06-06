"""Provide API service monitoring events behavior for NeuroCade."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend_common.auth import AuthContext
from backend_common.db import AppEvent
from backend_common.settings import get_settings


MAX_MESSAGE_LENGTH = 2000
MAX_DETAIL_STRING_LENGTH = 2000
MAX_DETAIL_ITEMS = 25
DUPLICATE_EVENT_WINDOW_SECONDS = 60
settings = get_settings()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...[truncated]"


def _sanitize_detail(value: Any, depth: int = 0) -> Any:
    """Return a size-bounded, JSON-safe event detail value."""
    if depth > 4:
        return "[truncated]"
    if isinstance(value, str):
        return _truncate(value, MAX_DETAIL_STRING_LENGTH)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _sanitize_detail(inner_value, depth + 1)
            for key, inner_value in list(value.items())[:MAX_DETAIL_ITEMS]
        }
    if isinstance(value, list | tuple):
        return [_sanitize_detail(item, depth + 1) for item in list(value)[:MAX_DETAIL_ITEMS]]
    return _truncate(str(value), MAX_DETAIL_STRING_LENGTH)


def sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize optional event details into a sanitized dictionary."""
    if not details:
        return {}
    sanitized = _sanitize_detail(details)
    return sanitized if isinstance(sanitized, dict) else {"value": sanitized}


def record_app_event(
    db: Session,
    *,
    source: str,
    level: str,
    event_type: str,
    message: str,
    context: AuthContext | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    details: dict[str, Any] | None = None,
) -> AppEvent:
    """Persist a sanitized monitoring event and enforce event retention."""
    user_id = context.user.id if context else None
    normalized_source = source[:64]
    normalized_level = level[:32]
    normalized_event_type = event_type[:128]
    normalized_message = _truncate(message, MAX_MESSAGE_LENGTH)
    normalized_method = method[:16] if method else None
    normalized_path = path[:1024] if path else None
    duplicate_cutoff = datetime.now(timezone.utc) - timedelta(seconds=DUPLICATE_EVENT_WINDOW_SECONDS)
    duplicate = (
        db.query(AppEvent)
        .filter(
            AppEvent.created_at >= duplicate_cutoff,
            AppEvent.user_id == user_id,
            AppEvent.source == normalized_source,
            AppEvent.level == normalized_level,
            AppEvent.event_type == normalized_event_type,
            AppEvent.message == normalized_message,
            AppEvent.method == normalized_method,
            AppEvent.path == normalized_path,
            AppEvent.status_code == status_code,
        )
        .order_by(AppEvent.created_at.desc(), AppEvent.id.desc())
        .first()
    )
    if duplicate is not None:
        return duplicate

    retention_days = max(settings.monitoring_event_retention_days, 1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db.query(AppEvent).filter(AppEvent.created_at < cutoff).delete(synchronize_session=False)
    event = AppEvent(
        user_id=user_id,
        source=normalized_source,
        level=normalized_level,
        event_type=normalized_event_type,
        message=normalized_message,
        method=normalized_method,
        path=normalized_path,
        status_code=status_code,
        details_json=sanitize_details(details),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
