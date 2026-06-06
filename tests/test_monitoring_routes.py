"""Test monitoring routes behavior for NeuroCade."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.routers import auth as auth_module  # noqa: E402
from api_service.routers import monitoring as monitoring_module  # noqa: E402
from api_service.routers.monitoring import ingest_client_error, monitoring_health, monitoring_summary  # noqa: E402
from api_service.schemas import MonitoringClientErrorRequest  # noqa: E402
from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import build_case_id  # noqa: E402
from backend_common.db import AppEvent, Artifact, ArtifactKind, AuditEvent, Base, Case, RoleEnum, User, Workspace, WorkspaceMembership  # noqa: E402


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
        status="active",
    )
    case = Case(
        id=build_case_id(workspace.id, "case-1"),
        workspace_id=workspace.id,
        owner_user_id=admin.id,
        title="case-1",
    )
    db_session.add_all([admin, member, workspace, case])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=admin.id, role=RoleEnum.owner, granted_by_user_id=admin.id))
    db_session.add(Artifact(case_id=case.id, kind=ArtifactKind.volume, name="orig.mgz", relative_path="output/workspaces/workspace-1/cases/case-1/orig.mgz"))
    db_session.commit()
    return db_session, AuthContext(user=admin, role=RoleEnum.owner, auth_mode="local"), member


def _patch_monitoring_checks(monkeypatch):
    """Replace external service checks with deterministic responses."""
    monkeypatch.setattr(monitoring_module.settings, "monitoring_admin_user_ids", "admin-user")
    monkeypatch.setattr(monitoring_module.settings, "monitoring_active_window_minutes", 15)
    monkeypatch.setattr(
        monitoring_module,
        "_check_redis",
        lambda: (
            monitoring_module._service_status("Redis", "ok", details={"status": "ok", "connected_clients": 1}),
            {"status": "ok", "connected_clients": 1},
        ),
    )
    monkeypatch.setattr(
        monitoring_module,
        "_check_api_celery",
        lambda: (
            monitoring_module._service_status("API worker", "ok", details={"active": 0, "queued": 0, "workers": ["worker-1"]}),
            {"status": "ok", "active": 0, "queued": 0, "workers": ["worker-1"]},
        ),
    )

    async def fake_fastsurfer_queue():
        return (
            monitoring_module._service_status("NeuroCade FastSurfer queue", "ok", details={"active": 1, "queued": 2, "total": 3}),
            {"status": "ok", "active": 1, "queued": 2, "total": 3},
        )

    monkeypatch.setattr(monitoring_module, "_check_fastsurfer_queue", fake_fastsurfer_queue)


def test_monitoring_summary_counts_recent_session_bootstraps(seeded_monitoring_context, monkeypatch):
    db_session, context, member = seeded_monitoring_context
    _patch_monitoring_checks(monkeypatch)
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            AuditEvent(user_id=context.user.id, action="session.bootstrap", details_json={}, created_at=now),
            AuditEvent(user_id=member.id, action="session.bootstrap", details_json={}, created_at=now - timedelta(hours=2)),
            AppEvent(source="backend", level="error", event_type="backend.http_error", message="failed", details_json={}, created_at=now),
        ]
    )
    db_session.commit()

    summary = asyncio.run(monitoring_summary(db=db_session, context=context))

    assert summary.status == "ok"
    assert summary.totals["users"] == 2
    assert summary.totals["cases"] == 1
    assert summary.totals["artifacts"] == 1
    assert summary.totals["recently_active_users"] == 1
    assert summary.active_users[0].id == "admin-user"
    assert summary.celery["fastsurfer_worker"]["total"] == 3
    assert summary.recent_errors[0].message == "failed"


def test_monitoring_summary_requires_configured_admin(seeded_monitoring_context, monkeypatch):
    db_session, _context, member = seeded_monitoring_context
    monkeypatch.setattr(monitoring_module.settings, "monitoring_admin_user_ids", "admin-user")
    context = AuthContext(user=member, role=RoleEnum.user, auth_mode="local")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(monitoring_summary(db=db_session, context=context))

    assert exc_info.value.status_code == 403


def test_monitoring_health_returns_service_status_without_summary_counts(seeded_monitoring_context, monkeypatch):
    db_session, context, _member = seeded_monitoring_context
    _patch_monitoring_checks(monkeypatch)

    health = asyncio.run(monitoring_health(db=db_session, context=context))

    assert health.status == "ok"
    assert [service.name for service in health.services] == [
        "API service",
        "Postgres",
        "Redis",
        "API worker",
        "NeuroCade FastSurfer queue",
    ]
    assert health.redis["status"] == "ok"
    assert health.celery["fastsurfer_worker"]["total"] == 3
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


def test_session_bootstrap_exposes_monitoring_feature_for_admin(seeded_monitoring_context, monkeypatch):
    db_session, context, _member = seeded_monitoring_context
    monkeypatch.setattr(auth_module.settings, "monitoring_admin_user_ids", "admin-user")

    session = auth_module.session_bootstrap(db=db_session, context=context)

    assert session.features["monitoring_dashboard"] is True
