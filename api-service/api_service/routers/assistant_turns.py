"""Assistant turn routes."""

from __future__ import annotations

import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.requests import Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api_service.assistant.conversation_store import thread_key
from api_service.assistant.runtime import assistant_runtime
from api_service.assistant.turn_manager import assistant_turn_manager
from api_service.assistant.turn_streaming import record_assistant_turn_event, stream_assistant_turn
from api_service.chat_limits import chat_request_guard
from api_service.deps import get_context, get_db
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.policies import require_case_read, require_workspace_read
from api_service.runtime import logger
from api_service.schemas import AssistantActiveTurnResponse, AssistantTurnCancelResponse, AssistantTurnRequest
from backend_common.auth import AuthContext

router = APIRouter(tags=["assistant-turns"])


@router.post("/api/app/assistant/turns")
async def create_assistant_turn(
    request: Request,
    payload: AssistantTurnRequest,
    context: AuthContext = Depends(get_context),
) -> StreamingResponse:
    private_thread_key = thread_key(
        user_id=context.user.id,
        scope=payload.scope,
        workspace_id=payload.workspace_id,
        case_id=payload.case_id,
    )
    rate_key = await chat_request_guard.acquire(
        f"user:{context.user.id}", thread_key=private_thread_key
    )
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
    try:
        return await stream_assistant_turn(
            request=request,
            payload=payload,
            runtime=assistant_runtime,
            request_id=request_id,
            rate_key=rate_key,
            thread_key=private_thread_key,
            started_at=started_at,
            request_details=request_details,
            context=context,
            request_guard=chat_request_guard,
        )
    except Exception:
        await chat_request_guard.release(rate_key, thread_key=private_thread_key)
        raise


def _authorized_thread_key(
    db: Session,
    context: AuthContext,
    *,
    workspace_id: str,
    scope: str,
    case_id: str | None,
) -> str:
    if scope == "case":
        if not case_id:
            raise HTTPException(status_code=400, detail="Case scope requires case_id")
        _case, _workspace, role, _case_dir = get_case_for_user(
            db,
            case_id,
            context.user.id,
            workspace_id=workspace_id,
        )
        require_case_read(role)
    elif scope == "workspace":
        _workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
        require_workspace_read(role)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported assistant scope: {scope}")
    return thread_key(
        user_id=context.user.id,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )


@router.get("/api/app/assistant/turns/active", response_model=AssistantActiveTurnResponse)
async def get_active_assistant_turn(
    workspace_id: str,
    scope: str = "case",
    case_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> AssistantActiveTurnResponse:
    private_thread_key = _authorized_thread_key(
        db,
        context,
        workspace_id=workspace_id,
        scope=scope,
        case_id=case_id,
    )
    active = await assistant_turn_manager.active(private_thread_key)
    if active is None:
        return AssistantActiveTurnResponse(active=False)
    return AssistantActiveTurnResponse(
        active=True,
        turn_id=active.turn_id,
        elapsed_seconds=max(0.0, time.monotonic() - active.started_at),
        activity=getattr(active, "activity", None),
    )


@router.post("/api/app/assistant/turns/{turn_id}/cancel", response_model=AssistantTurnCancelResponse)
async def cancel_assistant_turn(
    turn_id: str,
    workspace_id: str,
    scope: str = "case",
    case_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> AssistantTurnCancelResponse:
    private_thread_key = _authorized_thread_key(
        db,
        context,
        workspace_id=workspace_id,
        scope=scope,
        case_id=case_id,
    )
    canceled = await assistant_turn_manager.cancel(turn_id=turn_id, thread_key=private_thread_key)
    return AssistantTurnCancelResponse(
        status="canceling" if canceled else "not_active",
        turn_id=turn_id,
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
        "tool_approval_count": len(payload.tool_approvals),
        "persist": persist,
    }
