"""Test detached in-process assistant turn lifecycle behavior."""

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.requests import Request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant import turn_streaming as turn_streaming_module  # noqa: E402
from api_service.assistant.turn_manager import AssistantTurnManager  # noqa: E402
from api_service.schemas import AssistantTurnRequest  # noqa: E402

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import RoleEnum, User  # noqa: E402


@pytest.mark.asyncio
async def test_detaching_subscriber_keeps_turn_running():
    manager = AssistantTurnManager()
    continue_turn = asyncio.Event()
    producer_finished = asyncio.Event()
    finalized = asyncio.Event()

    async def producer(publish):
        await publish("event: reasoning\ndata: {}\n\n")
        await continue_turn.wait()
        producer_finished.set()

    async def finalizer():
        finalized.set()

    managed, queue = await manager.start(
        turn_id="turn-1",
        thread_key="thread-1",
        producer=producer,
        finalizer=finalizer,
    )
    assert await queue.get() == "event: reasoning\ndata: {}\n\n"

    await manager.detach("turn-1", queue)
    assert managed.task is not None
    assert not managed.task.done()
    assert await manager.active("thread-1") is managed

    await manager.update_activity(
        "turn-1",
        {
            "kind": "workflow",
            "label": "SynthSeg",
            "blocking": True,
            "run_id": "run-1",
            "mode": "synchronous",
            "device": "cpu",
        },
    )
    assert managed.activity.label == "SynthSeg"
    assert managed.activity.blocking is True
    assert managed.activity.device == "cpu"

    await manager.update_activity(
        "turn-1",
        {
            "kind": "image",
            "label": "vnmd/freesurfer:8.2",
            "blocking": True,
            "phase": "downloading",
            "progress": 0.5,
            "completed_layers": 5,
            "total_layers": 10,
        },
    )
    assert managed.activity.kind == "image"
    assert managed.activity.progress == 0.5

    continue_turn.set()
    await managed.task

    assert producer_finished.is_set()
    assert finalized.is_set()
    assert await manager.active("thread-1") is None


@pytest.mark.asyncio
async def test_cancel_requires_matching_thread_and_finalizes_turn():
    manager = AssistantTurnManager()
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def producer(_publish):
        started.set()
        await asyncio.Event().wait()

    async def finalizer():
        finalized.set()

    managed, _queue = await manager.start(
        turn_id="turn-2",
        thread_key="thread-2",
        producer=producer,
        finalizer=finalizer,
    )
    await started.wait()

    assert not await manager.cancel(turn_id="turn-2", thread_key="another-thread")
    assert await manager.cancel(turn_id="turn-2", thread_key="thread-2")
    assert managed.task is not None
    await asyncio.gather(managed.task, return_exceptions=True)

    assert finalized.is_set()
    assert await manager.active("thread-2") is None


@pytest.mark.asyncio
async def test_manager_rejects_second_turn_for_same_thread():
    manager = AssistantTurnManager()
    continue_turn = asyncio.Event()

    async def producer(_publish):
        await continue_turn.wait()

    async def finalizer():
        return None

    first, _queue = await manager.start(
        turn_id="turn-3",
        thread_key="thread-3",
        producer=producer,
        finalizer=finalizer,
    )
    with pytest.raises(RuntimeError, match="turn-3"):
        await manager.start(
            turn_id="turn-4",
            thread_key="thread-3",
            producer=producer,
            finalizer=finalizer,
        )

    continue_turn.set()
    assert first.task is not None
    await first.task


@pytest.mark.asyncio
async def test_closing_stream_detaches_without_canceling_background_turn(monkeypatch):
    manager = AssistantTurnManager()
    continue_turn = asyncio.Event()
    runtime_started = asyncio.Event()
    released = asyncio.Event()

    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class FakeRuntime:
        async def run_chat(self, **kwargs):
            runtime_started.set()
            await kwargs["event_sink"]("reasoning", {"summary": "working"})
            await continue_turn.wait()
            return {"message": {"role": "assistant", "content": "finished"}}

    class FakeGuard:
        async def release(self, key, *, thread_key=None):
            assert key == "rate-key"
            assert thread_key == "thread-stream"
            released.set()

    monkeypatch.setattr(turn_streaming_module, "SessionLocal", DummySession)
    monkeypatch.setattr(turn_streaming_module, "record_assistant_turn_event", lambda **_kwargs: None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/app/assistant/turns",
            "raw_path": b"/api/app/assistant/turns",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
        }
    )
    payload = AssistantTurnRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "continue in background"}],
            "workspace_id": "workspace-1",
            "gui_session_id": "gui-1",
            "scope": "workspace",
        }
    )
    context = AuthContext(
        user=User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User"),
        role=RoleEnum.owner,
        auth_mode="local",
    )

    response = await turn_streaming_module.stream_assistant_turn(
        request=request,
        payload=payload,
        runtime=FakeRuntime(),  # type: ignore[arg-type]
        request_id="turn-stream",
        rate_key="rate-key",
        thread_key="thread-stream",
        started_at=0.0,
        request_details={},
        context=context,
        request_guard=FakeGuard(),  # type: ignore[arg-type]
        manager=manager,
    )
    iterator = cast(Any, response.body_iterator)
    first_chunk = await anext(iterator)
    assert "event: reasoning" in first_chunk
    await iterator.aclose()

    active = await manager.active("thread-stream")
    assert runtime_started.is_set()
    assert active is not None
    assert active.task is not None and not active.task.done()
    assert not released.is_set()

    continue_turn.set()
    await active.task

    assert released.is_set()
    assert await manager.active("thread-stream") is None
