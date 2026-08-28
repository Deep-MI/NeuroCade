"""Test assistant turn streaming routes behavior for NeuroCade."""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import main as main_module  # noqa: E402
from api_service import middleware as middleware_module  # noqa: E402
from api_service.assistant import turn_streaming as assistant_turn_streaming_module  # noqa: E402
from api_service.chat_limits import ChatRequestGuard  # noqa: E402
from api_service.deps import get_context  # noqa: E402
from api_service.routers import assistant as assistant_router  # noqa: E402
from api_service.routers import assistant_turns as assistant_turns_module  # noqa: E402

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import Base, RoleEnum, User  # noqa: E402


@pytest.fixture()
def test_client(tmp_path, monkeypatch):
    """Provide a test client backed by an isolated SQLite app database."""
    original_lifespan = main_module.app.router.lifespan_context
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'assistant-turn-streaming.sqlite'}", future=True)
    Base.metadata.create_all(bind=engine)
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with test_session_local() as db:
        user = User(
            id="user-test",
            external_auth_id="user-test",
            email="user@example.com",
            full_name="User",
        )
        db.add_all([user])
        db.commit()
    monkeypatch.setattr(main_module, "SessionLocal", test_session_local)
    monkeypatch.setattr(assistant_turn_streaming_module, "SessionLocal", test_session_local)

    @asynccontextmanager
    async def noop_lifespan(_app):
        """Skip application startup tasks during route tests."""
        yield

    main_module.app.router.lifespan_context = noop_lifespan
    try:
        with TestClient(main_module.app, base_url="http://localhost") as client:
            yield client
    finally:
        main_module.app.router.lifespan_context = original_lifespan
        main_module.app.dependency_overrides.clear()
        engine.dispose()


def _dummy_context() -> AuthContext:
    """Return an authenticated owner context for route dependency overrides."""
    user = User(id="user-test", external_auth_id="user-test", email="user@example.com", full_name="User")
    return AuthContext(user=user, role=RoleEnum.owner, auth_mode="local")


def test_assistant_turn_rejects_malformed_or_caller_supplied_history(test_client):
    main_module.app.dependency_overrides[get_context] = _dummy_context
    malformed = test_client.post(
        "/api/app/assistant/turns",
        content="{",
        headers={"Content-Type": "application/json"},
    )
    history = test_client.post(
        "/api/app/assistant/turns",
        json={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "caller-supplied answer"},
            ]
        },
    )

    assert malformed.status_code == 422
    assert history.status_code == 422


def test_app_create_assistant_turn_streams_done_event(monkeypatch, test_client):
    async def fake_run_chat(**_kwargs):
        return {"message": {"role": "assistant", "content": "ready"}}

    monkeypatch.setattr(assistant_turns_module.assistant_runtime, "run_chat", fake_run_chat)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    response = test_client.post(
        "/api/app/assistant/turns",
        json={
                "messages": [{"role": "user", "content": "Reply with the single word ready."}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-test",
                "scope": "workspace",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Assistant-Turn-Id"]
    assert "event: done" in response.text
    assert "\"ready\"" in response.text


def test_app_create_assistant_turn_streams_interim_assistant_message(monkeypatch, test_client):
    async def fake_run_chat(**kwargs):
        await kwargs["event_sink"](
            "assistant_message",
            {"content": "The first tool route failed. I'll try a fallback.", "round": 2},
        )
        return {"message": {"role": "assistant", "content": "fallback finished"}}

    monkeypatch.setattr(assistant_turns_module.assistant_runtime, "run_chat", fake_run_chat)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    response = test_client.post(
        "/api/app/assistant/turns",
        json={
                "messages": [{"role": "user", "content": "Try a fallback."}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-test",
                "scope": "workspace",
        },
    )

    assert response.status_code == 200
    assert "event: assistant_message" in response.text
    assert "The first tool route failed. I'll try a fallback." in response.text
    assert "event: done" in response.text
    assert "fallback finished" in response.text


def test_app_create_assistant_turn_streams_timeout_event(monkeypatch, test_client):
    async def fake_run_chat(**_kwargs):
        await asyncio.sleep(1)
        return {"message": {"role": "assistant", "content": "too late"}}

    monkeypatch.setattr(assistant_turns_module.assistant_runtime, "run_chat", fake_run_chat)
    monkeypatch.setattr(assistant_turn_streaming_module.settings, "assistant_turn_timeout_seconds", 0.01)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    response = test_client.post(
        "/api/app/assistant/turns",
        json={
                "messages": [{"role": "user", "content": "Hang forever"}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-test",
                "scope": "workspace",
        },
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "assistant_timeout" in response.text
    assert "timed out" in response.text


def test_active_assistant_turn_status_and_cancel(monkeypatch, test_client):
    class FakeManager:
        canceled: tuple[str, str] | None = None

        async def active(self, thread_key):
            assert thread_key == "private-thread"
            return SimpleNamespace(
                turn_id="turn-active",
                started_at=10.0,
                activity={"kind": "tool", "label": "case_file_tree", "blocking": True},
            )

        async def cancel(self, *, turn_id, thread_key):
            self.canceled = (turn_id, thread_key)
            return True

    manager = FakeManager()
    monkeypatch.setattr(assistant_turns_module, "assistant_turn_manager", manager)
    monkeypatch.setattr(assistant_turns_module, "_authorized_thread_key", lambda *_args, **_kwargs: "private-thread")
    monkeypatch.setattr(assistant_turns_module.time, "monotonic", lambda: 15.5)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    active_response = test_client.get(
        "/api/app/assistant/turns/active",
        params={"workspace_id": "workspace-1", "scope": "workspace"},
    )
    cancel_response = test_client.post(
        "/api/app/assistant/turns/turn-active/cancel",
        params={"workspace_id": "workspace-1", "scope": "workspace"},
        json={},
    )

    assert active_response.status_code == 200
    assert active_response.json() == {
        "active": True,
        "turn_id": "turn-active",
        "elapsed_seconds": 5.5,
        "activity": {
            "kind": "tool",
            "label": "case_file_tree",
            "blocking": True,
            "run_id": None,
            "mode": None,
            "device": None,
            "phase": None,
            "progress": None,
            "completed_layers": None,
            "total_layers": None,
            "current_bytes": None,
            "total_bytes": None,
            "disk_free_bytes": None,
            "disk_warning": None,
            "reclaimable_storage": None,
            "stalled_seconds": None,
            "process_active": None,
        },
    }
    assert cancel_response.status_code == 200
    assert cancel_response.json() == {"status": "canceling", "turn_id": "turn-active"}
    assert manager.canceled == ("turn-active", "private-thread")


def test_case_share_routes_are_not_registered(test_client):
    create_response = test_client.post(
        "/api/app/cases/case-1-id/shares",
        json={"granted_to_user_id": "user-2", "permission_scope": "user"},
    )
    revoke_response = test_client.delete("/api/app/cases/case-1-id/shares/share-1")

    assert create_response.status_code == 404
    assert revoke_response.status_code == 404


def test_local_only_routes_hidden_outside_local_profile(monkeypatch, test_client):
    monkeypatch.setattr(middleware_module.settings, "deployment_profile", "internal")
    openapi_response = test_client.get("/api/app/openapi.json")
    docs_response = test_client.get("/api/app/docs")

    assert openapi_response.status_code == 404
    assert docs_response.status_code == 404


def test_app_create_assistant_turn_returns_429_when_rate_limited(monkeypatch, test_client):
    async def fake_run_chat(**_kwargs):
        return {"message": {"role": "assistant", "content": "ready"}}

    monkeypatch.setattr(assistant_turns_module.assistant_runtime, "run_chat", fake_run_chat)
    guard = ChatRequestGuard(
        max_requests_per_window=1,
        window_seconds=60,
        max_concurrent_requests=2,
        max_concurrent_per_key=1,
    )
    monkeypatch.setattr(assistant_turns_module, "chat_request_guard", guard)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    first_response = test_client.post(
        "/api/app/assistant/turns",
        json={
                "messages": [{"role": "user", "content": "Reply with ready."}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-test",
                "scope": "workspace",
        },
    )
    second_response = test_client.post(
        "/api/app/assistant/turns",
        json={
                "messages": [{"role": "user", "content": "Reply with ready."}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-test",
                "scope": "workspace",
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 429
    assert second_response.json()["detail"] == "Too many chat requests. Please wait and retry."


def test_provider_listing_redacts_internal_base_urls(test_client):
    main_module.app.dependency_overrides[get_context] = _dummy_context
    response = test_client.get("/api/app/providers")

    assert response.status_code == 200
    assert response.json()
    assert all("base_url" not in item for item in response.json())
    assert all("is_default" in item for item in response.json())


def test_clear_assistant_history_delete_route(monkeypatch, test_client):
    calls: list[dict[str, str | None]] = []

    async def fake_get_thread_key(*_args, **_kwargs):
        return None

    async def fake_clear_history(*_args, scope, workspace_id, case_id, **_kwargs):
        calls.append({"scope": scope, "workspace_id": workspace_id, "case_id": case_id})

    monkeypatch.setattr(assistant_router.assistant_runtime, "get_thread_key", fake_get_thread_key)
    monkeypatch.setattr(assistant_router.assistant_runtime, "clear_history", fake_clear_history)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    response = test_client.delete(
        "/api/app/assistant/history",
        params={
            "workspace_id": "workspace-1",
            "scope": "case",
            "case_id": "case-1-id",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "cleared"}
    assert calls == [{"scope": "case", "workspace_id": "workspace-1", "case_id": "case-1-id"}]


def test_assistant_history_returns_resumable_approval(monkeypatch, test_client):
    calls = 0
    approval = {
        "name": "write",
        "call_id": "call-1",
        "execution_id": "execution-1",
        "arguments": {"path": "report.txt", "content": "ready"},
        "digest": "a" * 64,
        "description": "write `report.txt`",
        "presentation": {
            "kind": "action",
            "action": "file_write",
            "title": "Write report.txt?",
            "description": "Write the supplied content.",
            "confirm_label": "Write file",
            "tone": "warning",
            "sections": [],
            "details": [],
        },
    }

    async def fake_history_state(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(thread_key="private-thread", messages=[], pending_approval=approval)

    monkeypatch.setattr(assistant_router.assistant_runtime, "get_history_state", fake_history_state)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    response = test_client.get(
        "/api/app/assistant/history",
        params={"workspace_id": "workspace-1", "scope": "workspace"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "private-thread",
        "messages": [],
        "pending_approval": approval,
    }
    assert calls == 1


def test_clear_assistant_history_rejects_active_background_turn(monkeypatch, test_client):
    class FakeManager:
        async def active(self, thread_key):
            assert thread_key == "private-thread"
            return SimpleNamespace(turn_id="turn-active")

    async def fake_get_thread_key(*_args, **_kwargs):
        return "private-thread"

    async def unexpected_clear(*_args, **_kwargs):
        raise AssertionError("active chat history must not be deleted")

    monkeypatch.setattr(assistant_router, "assistant_turn_manager", FakeManager())
    monkeypatch.setattr(assistant_router.assistant_runtime, "get_thread_key", fake_get_thread_key)
    monkeypatch.setattr(assistant_router.assistant_runtime, "clear_history", unexpected_clear)
    main_module.app.dependency_overrides[get_context] = _dummy_context

    response = test_client.delete(
        "/api/app/assistant/history",
        params={"workspace_id": "workspace-1", "scope": "workspace"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Stop the active assistant turn before clearing chat history."
