"""Assistant turn streaming helpers."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from typing import Any

from fastapi import HTTPException
from fastapi.requests import Request
from fastapi.responses import StreamingResponse

from api_service.assistant.runtime import AssistantRuntime
from api_service.chat_limits import chat_request_guard
from api_service.monitoring.events import record_app_event
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
            record_app_event(
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


async def stream_assistant_events(
    task: asyncio.Task[dict],
    queue: asyncio.Queue[str],
    *,
    request_id: str,
    context: AuthContext | None,
    method: str,
    path: str,
    started_at: float,
    details: dict,
):
    timeout_seconds = max(0.001, float(settings.assistant_turn_timeout_seconds))
    timeout_label = f"{timeout_seconds:g}"
    deadline = time.monotonic() + timeout_seconds
    event_counts: dict[str, int] = {}

    while True:
        if task.done() and queue.empty():
            break
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
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
            yield (
                "event: error\n"
                f"data: {json.dumps({'error': {'message': f'Assistant request timed out after {timeout_label} seconds. Please try again or narrow the request.', 'code': 'assistant_timeout'}})}\n\n"
            )
            return
        try:
            chunk = await asyncio.wait_for(queue.get(), timeout=min(0.25, remaining_seconds))
            _count_sse_event(request_id, started_at, event_counts, chunk)
            yield chunk
        except asyncio.TimeoutError:
            continue

    try:
        result = await task
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info("assistant.turn.completed request_id=%s elapsed_ms=%s", request_id, elapsed_ms)
        record_assistant_turn_event(
            event_type="assistant.turn.completed",
            message="Assistant request completed",
            context=context,
            method=method,
            path=path,
            status_code=200,
            details={
                **details,
                "elapsed_ms": elapsed_ms,
                "tool_call_count": len(result.get("tool_calls_log", []) or []),
            },
        )
        yield f"event: done\ndata: {json.dumps(result)}\n\n"
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
        yield f"event: error\ndata: {json.dumps({'error': {'message': message, 'code': exc.status_code}})}\n\n"
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
        yield f"event: error\ndata: {json.dumps({'error': {'message': str(exc), 'code': 'assistant_runtime_error'}})}\n\n"


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


def stream_assistant_turn(
    *,
    request: Request,
    payload: AssistantTurnRequest,
    runtime: AssistantRuntime,
    request_id: str,
    rate_key: str,
    started_at: float,
    request_details: dict[str, Any],
    context: AuthContext,
) -> StreamingResponse:
    async def event_stream():
        task: asyncio.Task[dict] | None = None
        completed = False
        try:
            queue: asyncio.Queue[str] = asyncio.Queue()

            async def emit(event: str, data: dict) -> None:
                await queue.put(f"event: {event}\ndata: {json.dumps(data)}\n\n")

            task = asyncio.create_task(
                _run_chat(
                    runtime=runtime,
                    payload=payload,
                    context=context,
                    emit=emit,
                    request_id=request_id,
                )
            )
            async for chunk in stream_assistant_events(
                task,
                queue,
                request_id=request_id,
                context=context,
                method=request.method,
                path=request.url.path,
                started_at=started_at,
                details=request_details,
            ):
                yield chunk
            completed = True
        finally:
            if task is not None and not task.done():
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.warning("assistant.turn.client_disconnected request_id=%s elapsed_ms=%s", request_id, elapsed_ms)
                record_assistant_turn_event(
                    event_type="assistant.turn.client_disconnected",
                    message="Assistant request was canceled before completion",
                    level="warning",
                    context=context,
                    method=request.method,
                    path=request.url.path,
                    status_code=499,
                    details={**request_details, "elapsed_ms": elapsed_ms, "completed": completed},
                )
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await chat_request_guard.release(rate_key)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
            messages=payload.messages,
            workspace_id=payload.workspace_id,
            case_id=payload.case_id,
            gui_session_id=payload.gui_session_id,
            gui_state_override=payload.gui_state_override,
            scope=payload.scope,
            provider=payload.provider,
            model=payload.model,
            event_sink=emit,
            diagnostic_request_id=request_id,
            persist=True,
        )
