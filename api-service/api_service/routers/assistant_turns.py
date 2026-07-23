"""Assistant turn routes."""

from __future__ import annotations

import time
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.requests import Request
from fastapi.responses import StreamingResponse

from api_service.assistant.runtime import assistant_runtime
from api_service.assistant.turn_streaming import record_assistant_turn_event, stream_assistant_turn
from api_service.chat_limits import chat_request_guard
from api_service.deps import get_context
from api_service.runtime import logger
from api_service.schemas import AssistantTurnRequest
from backend_common.auth import AuthContext

router = APIRouter(tags=["assistant-turns"])


@router.post("/api/app/assistant/turns")
async def create_assistant_turn(
    request: Request,
    context: AuthContext = Depends(get_context),
) -> StreamingResponse:
    payload = AssistantTurnRequest.model_validate(await request.json())
    rate_key = await chat_request_guard.acquire(f"user:{context.user.id}")
    request_id = uuid4().hex
    started_at = time.monotonic()
    request_details = _request_details(payload, request_id=request_id, persist=True)
    logger.info(
        "assistant.turn.started request_id=%s scope=%s workspace_id=%s case_id=%s",
        request_id,
        payload.scope,
        payload.workspace_id,
        payload.case_id,
    )
    record_assistant_turn_event(
        event_type="assistant.turn.started",
        message="Assistant request started",
        context=context,
        method=request.method,
        path=request.url.path,
        details=request_details,
    )
    return stream_assistant_turn(
        request=request,
        payload=payload,
        runtime=assistant_runtime,
        request_id=request_id,
        rate_key=rate_key,
        started_at=started_at,
        request_details=request_details,
        context=context,
    )


def _request_details(payload: AssistantTurnRequest, *, request_id: str, persist: bool) -> dict:
    return {
        "assistant_request_id": request_id,
        "scope": payload.scope,
        "workspace_id": payload.workspace_id,
        "case_id": payload.case_id,
        "message_count": len(payload.messages),
        "provider": payload.provider,
        "model": payload.model,
        "persist": persist,
    }
