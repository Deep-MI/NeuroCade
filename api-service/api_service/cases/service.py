"""Case domain helpers used by case routes."""

from __future__ import annotations

import json
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.case_storage import validate_case_title
from backend_common.db import Case, Run
from backend_common.deployment_policy import get_deployment_policy
from backend_common.run_statuses import ACTIVE_RUN_STATUSES


def validate_case_name_or_400(name: str) -> str:
    """Validate a case title or raise a 400 response with the validation error."""
    try:
        return validate_case_title(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def normalize_metadata_list(values: list[str] | str | None) -> list[str]:
    """Normalize metadata labels from JSON arrays, comma-delimited text, or lists."""
    if values is None or not isinstance(values, (list, str)):
        return []
    raw_values: Iterable[object]
    if isinstance(values, str):
        text = values.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Metadata lists must be JSON arrays or comma-separated strings") from exc
            raw_values = parsed if isinstance(parsed, list) else []
        else:
            raw_values = text.split(",")
    else:
        raw_values = values

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, str):
            continue
        label = item.strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def normalize_optional_text(value: str | None) -> str | None:
    """Trim optional text fields and collapse blank strings to None."""
    if value is None or not isinstance(value, str):
        return None
    return value.strip() or None


def latest_case_run(db: Session, case_id: str) -> Run | None:
    """Return the newest run recorded for a case."""
    return db.query(Run).filter(Run.case_id == case_id).order_by(Run.created_at.desc(), Run.id.desc()).first()


def raise_case_conflict(exc: IntegrityError, detail: str) -> None:
    """Raise a 409 response chained from an integrity conflict."""
    raise HTTPException(status_code=409, detail=detail) from exc


def ensure_case_not_active(db: Session, case: Case) -> None:
    """Reject case changes while an run is active."""
    active_run = (
        db.query(Run)
        .filter(Run.case_id == case.id, Run.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(Run.created_at.desc(), Run.id.desc())
        .first()
    )
    if active_run is not None:
        raise HTTPException(status_code=409, detail="Cannot modify a case that is currently running")


def require_uploads_enabled() -> None:
    """Reject upload requests when deployment policy disables uploads."""
    if not get_deployment_policy(settings).uploads_enabled:
        raise HTTPException(status_code=403, detail="Uploads are disabled for this deployment")


def require_mutations_enabled() -> None:
    """Reject mutating requests when deployment policy disables destructive actions."""
    if not get_deployment_policy(settings).destructive_actions_enabled:
        raise HTTPException(status_code=403, detail="This action is disabled for this deployment")
