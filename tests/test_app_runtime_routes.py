"""Test app runtime routes behavior for NeuroCade."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import main as main_module  # noqa: E402
from api_service.deps import get_context, get_db  # noqa: E402
from api_service.routers import app_runtime as app_runtime_module  # noqa: E402

from backend_common.db import Artifact, ArtifactKind, Base  # noqa: E402
from tests.factories import seed_workspace_context  # noqa: E402

CASE_ID = "workspace-1__case-1"


@pytest.fixture()
def db_session():
    """Create an isolated in-memory database session."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_context(db_session):
    """Seed a personal workspace, case, and owner auth context."""
    context, _workspace, _cases = seed_workspace_context(
        db_session,
        workspace_id="workspace-1",
        case_specs=(("case-1", "case-1"),),
    )
    return db_session, context


@pytest.fixture()
def test_client(seeded_context):
    """Create a test client with seeded app dependencies."""
    db_session, context = seeded_context
    startup_handlers = list(main_module.app.router.on_startup)
    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.on_startup.clear()
    main_module.app.dependency_overrides[get_db] = lambda: db_session
    main_module.app.dependency_overrides[get_context] = lambda: context

    @asynccontextmanager
    async def noop_lifespan(_app):
        """Skip app startup side effects for route tests."""
        yield

    main_module.app.router.lifespan_context = noop_lifespan
    try:
        with TestClient(main_module.app, base_url="http://localhost") as client:
            yield client
    finally:
        main_module.app.router.on_startup[:] = startup_handlers
        main_module.app.router.lifespan_context = original_lifespan
        main_module.app.dependency_overrides.clear()


def test_gui_state_sync_resolves_case_scope_from_current_case_id(monkeypatch, test_client):
    calls: list[dict] = []

    async def fake_sync_gui_state(payload: dict, *, gui_state_key: str | None = None) -> dict:
        """Capture GUI sync calls and echo the current case id."""
        calls.append({"payload": payload, "gui_state_key": gui_state_key})
        return {"status": "success", "current_state": {"current_case_id": payload.get("current_case_id")}}

    monkeypatch.setattr(app_runtime_module.runtime_service, "sync_gui_state", fake_sync_gui_state)

    response = test_client.post(
        "/api/app/gui/state",
        json={
            "current_case_id": CASE_ID,
            "gui_session_id": "gui-session-1",
            "is_job_running": False,
            "layers": [
                {
                    "id": "orig.mgz",
                    "filename": "orig.mgz",
                    "name": "orig",
                    "type": "intensity",
                    "loaded": True,
                    "visible": True,
                    "opacity": 1,
                    "display": {},
                }
            ],
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "payload": {
                "current_case_id": CASE_ID,
                "current_workspace_id": "workspace-1",
                "is_job_running": False,
                "layers": [
                    {
                        "id": "orig.mgz",
                        "filename": "orig.mgz",
                        "name": "orig",
                        "type": "intensity",
                        "loaded": True,
                        "visible": True,
                        "opacity": 1,
                        "display": {},
                    }
                ],
                "acknowledged_command_ids": [],
            },
            "gui_state_key": f"user:user-1|workspace:workspace-1|case:{CASE_ID}|session:gui-session-1",
        }
    ]
    assert response.json() == {
        "status": "success",
        "current_state": {"current_case_id": CASE_ID},
        "commands": [],
    }


def test_runtime_resource_serves_lut_from_local_file(monkeypatch, tmp_path, test_client):
    lut_path = tmp_path / "FreeSurferColorLUT.txt"
    lut_path.write_text("lut-data", encoding="utf-8")
    monkeypatch.setattr(app_runtime_module, "LUT_PATH", lut_path)
    response = test_client.get("/api/app/static/luts/freesurfer")

    assert response.status_code == 200
    assert response.text == "lut-data"


def test_gui_state_sync_resolves_output_resource_descriptor_to_artifact_path(monkeypatch, seeded_context, test_client):
    db_session, _context = seeded_context
    artifact = Artifact(
        id="artifact-1",
        case_id=CASE_ID,
        workspace_id="workspace-1",
        kind=ArtifactKind.volume,
        name="orig.mgz",
        relative_path="output/workspaces/workspace-1/cases/case-1/mri/orig.mgz",
        mime_type="application/octet-stream",
        size_bytes=6,
        metadata_json={},
    )
    db_session.add(artifact)
    db_session.commit()

    async def fake_sync_gui_state(payload: dict, *, gui_state_key: str | None = None) -> dict:
        return {
            "commands": [
                {
                    "id": "command-1",
                    "type": "load_layer",
                    "created_at": "2026-07-28T00:00:00Z",
                    "payload": {
                        "resource": {"kind": "output", "path": "outputs/workspaces/workspace-1/cases/case-1/mri/orig.mgz"},
                        "filename": "orig.mgz",
                        "name": "orig",
                        "type": "intensity",
                    },
                }
            ]
        }

    monkeypatch.setattr(app_runtime_module.runtime_service, "sync_gui_state", fake_sync_gui_state)

    response = test_client.post(
        "/api/app/gui/state",
        json={"current_case_id": CASE_ID, "gui_session_id": "gui-session-1"},
    )

    assert response.status_code == 200
    assert response.json()["commands"][0]["payload"]["download_path"] == "/artifacts/artifact-1/download"
