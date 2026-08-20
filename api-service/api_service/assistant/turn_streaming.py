"""Assistant turn streaming helpers."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import HTTPException
from fastapi.requests import Request
from fastapi.responses import StreamingResponse

from api_service.assistant.runtime import AssistantRuntime
from api_service.assistant.turn_manager import AssistantTurnManager, assistant_turn_manager
from api_service.chat_limits import ChatRequestGuard
from api_service.monitoring.events import record_app_event_best_effort
from api_service.runtime import logger, settings
from api_service.schemas import AssistantTurnRequest
from backend_common.auth import AuthContext
from backend_common.db import SessionLocal


def record_assistant_turn_event(
    *,
    event_type: str,
    message: str,
    level: str = "info",
    context: AuthContext | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    details: dict | None = None,
) -> None:
    try:
        with SessionLocal() as db:
            record_app_event_best_effort(
                db,
                source="backend",
                level=level,
                event_type=event_type,
                message=message,
                context=context,
                method=method,
                path=path,
                status_code=status_code,
                details=details,
            )
    except Exception as exc:  # pragma: no cover - diagnostics must not break assistant turns
        logger.warning("Failed to record assistant turn event %s: %s", event_type, exc)


async def stream_assistant_events(queue: asyncio.Queue[str | None], *, request_id: str, started_at: float):
    """Yield events for one attached client without owning the background turn."""
    event_counts: dict[str, int] = {}
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        _count_sse_event(request_id, started_at, event_counts, chunk)
        yield chunk


async def _produce_events(
    publish,
    *,
    runtime: AssistantRuntime,
    payload: AssistantTurnRequest,
    context: AuthContext,
    request_id: str,
    method: str,
    path: str,
    started_at: float,
    details: dict,
    activity_sink=None,
) -> None:
    async def emit(event: str, data: dict) -> None:
        if event == "activity" and activity_sink is not None:
            await activity_sink(data)
        await publish(f"event: {event}\ndata: {json.dumps(data)}\n\n")

    try:
        timeout_seconds = max(0.001, float(settings.assistant_turn_timeout_seconds))
        async with asyncio.timeout(timeout_seconds):
            result = await _run_chat(runtime=runtime, payload=payload, context=context, emit=emit, request_id=request_id)
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info("assistant.turn.completed request_id=%s elapsed_ms=%s", request_id, elapsed_ms)
        record_assistant_turn_event(
            event_type="assistant.turn.completed",
            message="Assistant request completed",
            context=context,
            method=method,
            path=path,
            status_code=200,
            details={**details, "elapsed_ms": elapsed_ms, "tool_call_count": len(result.get("tool_calls_log", []) or [])},
        )
        await emit("done", result)
    except TimeoutError:
        timeout_seconds = max(0.001, float(settings.assistant_turn_timeout_seconds))
        timeout_label = f"{timeout_seconds:g}"
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.warning("assistant.turn.timeout request_id=%s elapsed_ms=%s", request_id, elapsed_ms)
        record_assistant_turn_event(
            event_type="assistant.turn.timeout",
            message="Assistant request timed out",
            level="warning",
            context=context,
            method=method,
            path=path,
            status_code=504,
            details={**details, "elapsed_ms": elapsed_ms, "timeout_seconds": timeout_seconds},
        )
        await emit(
            "error",
            {
                "error": {
                    "message": f"Assistant request timed out after {timeout_label} seconds. Please try again or narrow the request.",
                    "code": "assistant_timeout",
                }
            },
        )
    except asyncio.CancelledError:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info("assistant.turn.canceled request_id=%s elapsed_ms=%s", request_id, elapsed_ms)
        record_assistant_turn_event(
            event_type="assistant.turn.canceled",
            message="Assistant request canceled",
            context=context,
            method=method,
            path=path,
            status_code=499,
            details={**details, "elapsed_ms": elapsed_ms},
        )
        await emit("error", {"error": {"message": "Assistant request was canceled.", "code": "assistant_canceled"}})
    except HTTPException as exc:
        message = exc.detail if isinstance(exc.detail, str) else "API request failed"
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.warning("assistant.turn.http_error request_id=%s status_code=%s elapsed_ms=%s", request_id, exc.status_code, elapsed_ms)
        record_assistant_turn_event(
            event_type="assistant.turn.failed",
            message=message,
            level="warning",
            context=context,
            method=method,
            path=path,
            status_code=exc.status_code,
            details={**details, "elapsed_ms": elapsed_ms, "error_code": exc.status_code},
        )
        await emit("error", {"error": {"message": message, "code": exc.status_code}})
    except Exception as exc:  # pragma: no cover - defensive streaming fallback
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.exception("Assistant turn stream failed")
        record_assistant_turn_event(
            event_type="assistant.turn.failed",
            message=str(exc),
            level="error",
            context=context,
            method=method,
            path=path,
            status_code=500,
            details={**details, "elapsed_ms": elapsed_ms, "error_type": type(exc).__name__},
        )
        await emit("error", {"error": {"message": str(exc), "code": "assistant_runtime_error"}})


def _count_sse_event(
    request_id: str,
    started_at: float,
    event_counts: dict[str, int],
    chunk: str,
) -> None:
    event_name = ""
    for line in chunk.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ").strip()
            break
    if not event_name:
        return
    event_counts[event_name] = event_counts.get(event_name, 0) + 1
    logger.info(
        "assistant.turn.sse_event request_id=%s event=%s count=%s elapsed_ms=%s",
        request_id,
        event_name,
        event_counts[event_name],
        int((time.monotonic() - started_at) * 1000),
    )


async def stream_assistant_turn(
    *,
    request: Request,
    payload: AssistantTurnRequest,
    runtime: AssistantRuntime,
    request_id: str,
    rate_key: str,
    thread_key: str,
    started_at: float,
    request_details: dict[str, Any],
    context: AuthContext,
    request_guard: ChatRequestGuard,
    manager: AssistantTurnManager = assistant_turn_manager,
) -> StreamingResponse:
    async def producer(publish) -> None:
        await _produce_events(
            publish,
            runtime=runtime,
            payload=payload,
            context=context,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            started_at=started_at,
            details=request_details,
            activity_sink=lambda activity: manager.update_activity(request_id, activity),
        )

    async def finalize() -> None:
        await request_guard.release(rate_key, thread_key=thread_key)

    _managed, queue = await manager.start(
        turn_id=request_id,
        thread_key=thread_key,
        producer=producer,
        finalizer=finalize,
    )

    async def event_stream():
        try:
            async for chunk in stream_assistant_events(queue, request_id=request_id, started_at=started_at):
                yield chunk
        finally:
            await manager.detach(request_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Assistant-Turn-Id": request_id},
    )


async def _run_chat(
    *,
    runtime: AssistantRuntime,
    payload: AssistantTurnRequest,
    context: AuthContext,
    emit,
    request_id: str,
) -> dict:
    with SessionLocal() as db:
        return await runtime.run_chat(
            db=db,
            context=context,
            messages=[message.model_dump() for message in payload.messages],
            workspace_id=payload.workspace_id,
            case_id=payload.case_id,
            gui_session_id=payload.gui_session_id,
            gui_state_override=payload.gui_state_override,
            tool_approvals=[approval.model_dump() for approval in payload.tool_approvals],
            scope=payload.scope,
            provider=payload.provider,
            model=payload.model,
            event_sink=emit,
            diagnostic_request_id=request_id,
            persist=True,
        )
