"""Provide API service assistant behavior for NeuroCade."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.conversation_store import thread_key
from api_service.assistant.runtime import assistant_runtime
from api_service.assistant.turn_manager import assistant_turn_manager
from api_service.deps import get_context, get_db
from api_service.schemas import AssistantHistoryClearResponse, AssistantHistoryResponse
from backend_common.auth import AuthContext

router = APIRouter(prefix="/api/app", tags=["assistant"])


@router.get("/assistant/history", response_model=AssistantHistoryResponse)
async def assistant_history(
    workspace_id: str,
    scope: str = "case",
    case_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> AssistantHistoryResponse:
    """Return assistant messages and the thread key for a workspace scope."""
    history = await assistant_runtime.get_history_state(
        db,
        context,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )
    return AssistantHistoryResponse(
        thread_id=history.thread_key,
        messages=history.messages,
        pending_approval=history.pending_approval,
    )


@router.delete("/assistant/history", response_model=AssistantHistoryClearResponse)
async def clear_assistant_history(
    workspace_id: str,
    scope: str = "case",
    case_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> AssistantHistoryClearResponse:
    """Clear assistant messages for a workspace scope."""
    existing_thread_key = await assistant_runtime.get_thread_key(
        db,
        context,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )
    private_thread_key = existing_thread_key or thread_key(
        user_id=context.user.id, scope=scope, workspace_id=workspace_id, case_id=case_id
    )
    if await assistant_turn_manager.active(private_thread_key) is not None:
        raise HTTPException(status_code=409, detail="Stop the active assistant turn before clearing chat history.")
    await assistant_runtime.clear_history(
        db,
        context,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )
    return AssistantHistoryClearResponse(status="cleared")
