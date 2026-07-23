"""Provide API service assistant behavior for NeuroCade."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api_service.assistant.runtime import assistant_runtime
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
    messages = await assistant_runtime.list_history(
        db,
        context,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )
    thread_id = await assistant_runtime.get_thread_key(
        db,
        context,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )
    return AssistantHistoryResponse(thread_id=thread_id, messages=messages)


@router.delete("/assistant/history", response_model=AssistantHistoryClearResponse)
async def clear_assistant_history(
    workspace_id: str,
    scope: str = "case",
    case_id: str | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> AssistantHistoryClearResponse:
    """Clear assistant messages for a workspace scope."""
    await assistant_runtime.clear_history(
        db,
        context,
        scope=scope,
        workspace_id=workspace_id,
        case_id=case_id,
    )
    return AssistantHistoryClearResponse(status="cleared")
