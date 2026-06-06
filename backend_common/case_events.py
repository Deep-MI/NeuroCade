"""Small helpers for case activity events."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend_common.db import Case, CaseEvent


def record_case_event(
    db: Session,
    case: Case,
    event_type: str,
    *,
    user_id: str | None = None,
    artifact_id: str | None = None,
    details: dict | None = None,
) -> CaseEvent:
    """Add a case event row and return it."""
    event = CaseEvent(
        case_id=case.id,
        workspace_id=case.workspace_id,
        user_id=user_id,
        artifact_id=artifact_id,
        event_type=event_type,
        details_json=details or {},
    )
    db.add(event)
    return event
