"""Test monitoring routes behavior for NeuroCade."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.monitoring import events as monitoring_events_module  # noqa: E402
from api_service.routers import monitoring as monitoring_module  # noqa: E402
from api_service.routers.monitoring import ingest_client_error, monitoring_health, monitoring_summary  # noqa: E402
from api_service.schemas import MonitoringClientErrorRequest  # noqa: E402

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import (  # noqa: E402
    AppEvent,
    Artifact,
    ArtifactKind,
    AuditEvent,
    Base,
    Case,
    RoleEnum,
    User,
    Workspace,
    WorkspaceMembership,
)


@pytest.fixture()
def db_session():
    """Create an isolated in-memory database session."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_monitoring_context(db_session):
    """Seed monitoring route data and an owner auth context."""
    admin = User(id="admin-user", external_auth_id="admin-user", email="admin@example.com", full_name="Admin")
    member = User(id="member-user", external_auth_id="member-user", email="member@example.com", full_name="Member")
    workspace = Workspace(
        id="workspace-1",
        owner_user_id=admin.id,
        name="Monitoring Workspace",
        kind="shared",
        is_default=True,
    )
    case = Case(
        id="case-1-id",
        workspace_id=workspace.id,
        owner_user_id=admin.id,
        title="case-1",
    )
    db_session.add_all([admin, member, workspace, case])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=admin.id, role=RoleEnum.owner, granted_by_user_id=admin.id))
    db_session.add(Artifact(case_id=case.id, kind=ArtifactKind.volume, name="orig.mgz", relative_path="orig.mgz"))
    db_session.commit()
    return db_session, AuthContext(user=admin, role=RoleEnum.owner, auth_mode="local"), member


def _patch_monitoring_checks(monkeypatch):
    """Replace external service checks with deterministic responses."""
    monkeypatch.setattr(monitoring_module.settings, "monitoring_admin_user_ids", "admin-user")
    monkeypatch.setattr(monitoring_module.settings, "monitoring_active_window_minutes", 15)
    monkeypatch.setattr(
        monitoring_module,
        "_check_job_worker",
        lambda: (
            monitoring_module._service_status("Background jobs", "ok", details={"active": 0, "queued": 0, "total": 0}),
            {"status": "ok", "active": 0, "queued": 0, "total": 0},
        ),
    )

    def fake_fastsurfer_queue():
        return (
            monitoring_module._service_status("NeuroCade FastSurfer queue", "ok", details={"active": 1, "queued": 2, "total": 3}),
            {"status": "ok", "active": 1, "queued": 2, "total": 3},
        )

    monkeypatch.setattr(monitoring_module, "_check_fastsurfer_queue", fake_fastsurfer_queue)


def test_monitoring_summary_counts_recent_session_bootstraps(seeded_monitoring_context, monkeypatch):
    db_session, context, member = seeded_monitoring_context
    _patch_monitoring_checks(monkeypatch)
    now = datetime.now(UTC)
    db_session.add_all(
        [
            AuditEvent(user_id=context.user.id, action="session.bootstrap", details_json={}, created_at=now),
            AuditEvent(user_id=member.id, action="session.bootstrap", details_json={}, created_at=now - timedelta(hours=2)),
            AppEvent(source="backend", level="error", event_type="backend.http_error", message="failed", details_json={}, created_at=now),
        ]
    )
    db_session.commit()

    summary = monitoring_summary(db=db_session, context=context)

    assert summary.status == "ok"
    assert summary.totals["users"] == 2
    assert summary.totals["cases"] == 1
    assert summary.totals["artifacts"] == 1
    assert summary.totals["recently_active_users"] == 1
    assert summary.active_users[0].id == "admin-user"
    assert summary.jobs["fastsurfer_queue"]["total"] == 3
    assert summary.recent_errors[0].message == "failed"


def test_monitoring_summary_requires_configured_admin(seeded_monitoring_context, monkeypatch):
    db_session, _context, member = seeded_monitoring_context
    monkeypatch.setattr(monitoring_module.settings, "monitoring_admin_user_ids", "admin-user")
    context = AuthContext(user=member, role=RoleEnum.user, auth_mode="local")

    with pytest.raises(HTTPException) as exc_info:
        monitoring_summary(db=db_session, context=context)

    assert exc_info.value.status_code == 403


def test_monitoring_health_returns_service_status_without_summary_counts(seeded_monitoring_context, monkeypatch):
    db_session, context, _member = seeded_monitoring_context
    _patch_monitoring_checks(monkeypatch)

    health = monitoring_health(db=db_session, context=context)

    assert health.status == "ok"
    assert [service.name for service in health.services] == [
        "API service",
        "Database",
        "Background jobs",
        "NeuroCade FastSurfer queue",
    ]
    assert health.jobs["worker"]["status"] == "ok"
    assert health.jobs["fastsurfer_queue"]["total"] == 3
    assert not hasattr(health, "totals")


def test_client_error_ingestion_records_user_event(seeded_monitoring_context):
    db_session, context, _member = seeded_monitoring_context
    response = ingest_client_error(
        MonitoringClientErrorRequest(
            event_type="frontend.error_boundary",
            message="Viewer crashed",
            path="/workspaces/workspace-1/cases/case-1",
            details={"stack": "trace"},
        ),
        db=db_session,
        context=context,
    )

    event = db_session.query(AppEvent).one()
    assert response.status == "recorded"
    assert event.source == "frontend"
    assert event.user_id == "admin-user"
    assert event.path == "/workspaces/workspace-1/cases/case-1"
    assert event.details_json["stack"] == "trace"


def test_client_error_ingestion_is_best_effort(seeded_monitoring_context, monkeypatch):
    db_session, context, _member = seeded_monitoring_context
    monkeypatch.setattr(monitoring_module, "record_app_event_best_effort", lambda *_args, **_kwargs: None)

    response = ingest_client_error(
        MonitoringClientErrorRequest(
            event_type="frontend.error_boundary",
            message="Viewer crashed",
            path="/workspaces/workspace-1/cases/case-1",
        ),
        db=db_session,
        context=context,
    )

    assert response.status == "dropped"


def test_monitoring_retention_cleanup_runs_periodically(seeded_monitoring_context, monkeypatch):
    db_session, context, _member = seeded_monitoring_context
    monkeypatch.setattr(monitoring_events_module, "_next_retention_cleanup_at", 0.0)
    old_created_at = datetime.now(UTC) - timedelta(days=60)
    db_session.add(
        AppEvent(
            source="frontend",
            level="error",
            event_type="old.first",
            message="old first",
            details_json={},
            created_at=old_created_at,
        )
    )
    db_session.commit()

    monitoring_events_module.record_app_event(
        db_session,
        source="frontend",
        level="error",
        event_type="new.first",
        message="new first",
        context=context,
    )
    assert db_session.query(AppEvent).filter(AppEvent.event_type == "old.first").count() == 0

    db_session.add(
        AppEvent(
            source="frontend",
            level="error",
            event_type="old.second",
            message="old second",
            details_json={},
            created_at=old_created_at,
        )
    )
    db_session.commit()
    monitoring_events_module.record_app_event(
        db_session,
        source="frontend",
        level="error",
        event_type="new.second",
        message="new second",
        context=context,
    )

    assert db_session.query(AppEvent).filter(AppEvent.event_type == "old.second").count() == 1
