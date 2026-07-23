"""Provide API service monitoring behavior for NeuroCade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, text
from sqlalchemy.orm import Session

from api_service.deps import get_context, get_db
from api_service.jobs import job_manager
from api_service.monitoring.events import record_app_event
from api_service.monitoring.security import require_monitoring_admin
from api_service.runtime import settings
from api_service.runtime.service import runtime_service
from api_service.schemas import (
    MonitoringAuditEventSummary,
    MonitoringClientErrorRequest,
    MonitoringEventsResponse,
    MonitoringEventSummary,
    MonitoringHealth,
    MonitoringIngestResponse,
    MonitoringStatusItem,
    MonitoringSummary,
    MonitoringUserSummary,
)
from backend_common.auth import AuthContext
from backend_common.db import AppEvent, Artifact, AssistantScope, AuditEvent, Case, Run, User, Workspace
from backend_common.run_statuses import ACTIVE_RUN_STATUSES

router = APIRouter(prefix="/api/app/monitoring", tags=["monitoring"])

ServiceStatus = Literal["ok", "degraded", "down", "unknown"]
OverallStatus = Literal["ok", "degraded", "down"]


def _now() -> datetime:
    return datetime.now(UTC)


def _service_status(
    name: str,
    status: ServiceStatus,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> MonitoringStatusItem:
    """Create a normalized service health entry for monitoring responses."""
    return MonitoringStatusItem(name=name, status=status, message=message, details=details or {})


def _serialize_event(row: AppEvent, user: User | None = None) -> MonitoringEventSummary:
    """Convert an application event row into the public monitoring schema."""
    return MonitoringEventSummary(
        id=row.id,
        source=row.source,
        level=row.level,
        event_type=row.event_type,
        message=row.message,
        user_id=row.user_id,
        user_email=user.email if user else None,
        method=row.method,
        path=row.path,
        status_code=row.status_code,
        details=dict(row.details_json or {}),
        created_at=row.created_at,
    )


def _serialize_audit_event(row: AuditEvent, user: User | None = None) -> MonitoringAuditEventSummary:
    """Convert an audit event row into the public monitoring schema."""
    return MonitoringAuditEventSummary(
        id=row.id,
        action=row.action,
        user_id=row.user_id,
        user_email=user.email if user else None,
        case_id=row.case_id,
        artifact_id=row.artifact_id,
        details=dict(row.details_json or {}),
        created_at=row.created_at,
    )


def _check_database(db: Session) -> MonitoringStatusItem:
    """Verify that the database connection can execute a simple query."""
    try:
        db.execute(text("SELECT 1")).scalar_one()
    except Exception as exc:
        return _service_status("Database", "down", str(exc))
    return _service_status("Database", "ok")


def _check_job_worker() -> tuple[MonitoringStatusItem, dict[str, Any]]:
    """Report in-process background job worker health (replaces Celery/Redis)."""
    try:
        counts = job_manager.queue_status()
        payload = {
            "status": "ok",
            "active": int(counts.get("active", 0)),
            "queued": int(counts.get("queued", 0)),
            "total": int(counts.get("total", 0)),
        }
        return _service_status("Background jobs", "ok", details=payload), payload
    except Exception as exc:
        payload = {"status": "down", "error": str(exc), "active": 0, "queued": 0, "total": 0}
        return _service_status("Background jobs", "down", str(exc)), payload


async def _check_fastsurfer_queue() -> tuple[MonitoringStatusItem, dict[str, Any]]:
    """Fetch FastSurfer queue health from the runtime service."""
    try:
        payload = await runtime_service.fetch_queue_status()
        normalized = {
            "status": "ok",
            "active": int(payload.get("active", 0)),
            "queued": int(payload.get("queued", 0)),
            "total": int(payload.get("total", 0)),
        }
        return _service_status("NeuroCade FastSurfer queue", "ok", details=normalized), normalized
    except (RuntimeError, ValueError) as exc:
        payload = {"status": "down", "error": str(exc), "active": 0, "queued": 0, "total": 0}
        return _service_status("NeuroCade FastSurfer queue", "down", str(exc)), payload


def _active_users(db: Session, since: datetime) -> list[MonitoringUserSummary]:
    """List users with recent session activity since the given timestamp."""
    rows = (
        db.query(User, func.max(AuditEvent.created_at).label("last_seen_at"))
        .join(AuditEvent, AuditEvent.user_id == User.id)
        .filter(AuditEvent.action == "session.bootstrap", AuditEvent.created_at >= since)
        .group_by(User.id)
        .order_by(desc("last_seen_at"))
        .limit(50)
        .all()
    )
    return [
        MonitoringUserSummary(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            last_seen_at=last_seen_at,
        )
        for user, last_seen_at in rows
    ]


def _recent_errors(db: Session, limit: int = 10) -> list[MonitoringEventSummary]:
    """Return the latest application error and critical events."""
    rows = (
        db.query(AppEvent, User)
        .outerjoin(User, User.id == AppEvent.user_id)
        .filter(AppEvent.level.in_(("error", "critical")))
        .order_by(AppEvent.created_at.desc(), AppEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_event(event, user) for event, user in rows]


def _recent_activity(db: Session, limit: int = 10) -> list[MonitoringAuditEventSummary]:
    """Return the latest audit events for the monitoring dashboard."""
    rows = (
        db.query(AuditEvent, User)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_audit_event(event, user) for event, user in rows]


def _overall_status(services: list[MonitoringStatusItem]) -> OverallStatus:
    """Collapse individual service states into an overall health status."""
    if any(service.status == "down" for service in services):
        return "degraded"
    if any(service.status in {"degraded", "unknown"} for service in services):
        return "degraded"
    return "ok"


async def _build_summary(db: Session) -> MonitoringSummary:
    """Assemble dashboard totals, service health, and recent activity."""
    generated_at = _now()
    active_window_minutes = max(settings.monitoring_active_window_minutes, 1)
    active_since = generated_at - timedelta(minutes=active_window_minutes)
    users = _active_users(db, active_since)

    database_status = _check_database(db)
    job_worker_status, job_worker_payload = _check_job_worker()
    fastsurfer_status, fastsurfer_payload = await _check_fastsurfer_queue()
    services = [
        _service_status("API service", "ok"),
        database_status,
        job_worker_status,
        fastsurfer_status,
    ]

    totals = {
        "users": db.query(User).count(),
        "recently_active_users": len(users),
        "workspaces": db.query(Workspace).filter(Workspace.status == "active").count(),
        "cases": db.query(Case).count(),
        "artifacts": db.query(Artifact).count(),
        "active_runs": db.query(Run).filter(Run.status.in_(ACTIVE_RUN_STATUSES)).count(),
        "active_workspace_runs": db.query(Run)
        .filter(Run.scope_type == AssistantScope.workspace, Run.status.in_(ACTIVE_RUN_STATUSES))
        .count(),
        "errors_24h": db.query(AppEvent)
        .filter(AppEvent.level.in_(("error", "critical")), AppEvent.created_at >= generated_at - timedelta(hours=24))
        .count(),
    }

    return MonitoringSummary(
        generated_at=generated_at,
        status=_overall_status(services),
        active_window_minutes=active_window_minutes,
        totals=totals,
        active_users=users,
        services=services,
        jobs={"worker": job_worker_payload, "fastsurfer_queue": fastsurfer_payload},
        recent_errors=_recent_errors(db),
        recent_activity=_recent_activity(db),
    )


async def _build_health(db: Session) -> MonitoringHealth:
    """Assemble the lightweight monitoring health payload."""
    database_status = _check_database(db)
    job_worker_status, job_worker_payload = _check_job_worker()
    fastsurfer_status, fastsurfer_payload = await _check_fastsurfer_queue()
    services = [
        _service_status("API service", "ok"),
        database_status,
        job_worker_status,
        fastsurfer_status,
    ]
    return MonitoringHealth(
        generated_at=_now(),
        status=_overall_status(services),
        services=services,
        jobs={"worker": job_worker_payload, "fastsurfer_queue": fastsurfer_payload},
    )


@router.get("/summary", response_model=MonitoringSummary)
async def monitoring_summary(
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> MonitoringSummary:
    """Return the admin monitoring dashboard summary."""
    require_monitoring_admin(context)
    return await _build_summary(db)


@router.get("/health", response_model=MonitoringHealth)
async def monitoring_health(
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> MonitoringHealth:
    """Return current service health for monitoring admins."""
    require_monitoring_admin(context)
    return await _build_health(db)


@router.get("/events", response_model=MonitoringEventsResponse)
def monitoring_events(
    limit: int = Query(default=100, ge=1, le=500),
    source: str | None = Query(default=None),
    level: str | None = Query(default=None),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> MonitoringEventsResponse:
    """Return recent application and audit events with optional filters."""
    require_monitoring_admin(context)

    query = db.query(AppEvent, User).outerjoin(User, User.id == AppEvent.user_id)
    if source:
        query = query.filter(AppEvent.source == source)
    if level:
        query = query.filter(AppEvent.level == level)
    app_rows = query.order_by(AppEvent.created_at.desc(), AppEvent.id.desc()).limit(limit).all()

    audit_rows = (
        db.query(AuditEvent, User)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .all()
    )
    return MonitoringEventsResponse(
        events=[_serialize_event(event, user) for event, user in app_rows],
        audit_events=[_serialize_audit_event(event, user) for event, user in audit_rows],
    )


@router.post("/client-errors", response_model=MonitoringIngestResponse)
def ingest_client_error(
    request: MonitoringClientErrorRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> MonitoringIngestResponse:
    """Record a frontend error event from the authenticated client."""
    record_app_event(
        db,
        source="frontend",
        level=request.level,
        event_type=request.event_type,
        message=request.message,
        context=context,
        path=request.path,
        details=request.details,
    )
    return MonitoringIngestResponse(status="recorded")
