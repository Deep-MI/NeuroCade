"""Assistant runtime coordination."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.conversation_store import AssistantConversationStore, done_payload  # noqa: E402
from api_service.assistant.loop import AssistantLoop  # noqa: E402
from api_service.assistant.tools import AssistantToolBuilder  # noqa: E402
from api_service.runtime.service import RuntimeService, runtime_service  # noqa: E402
from api_service.schemas import ChatMessageSummary  # noqa: E402
from api_service.helpers import get_case_for_user, get_workspace_for_user  # noqa: E402
from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import AssistantScope, Case, Workspace  # noqa: E402
from backend_common.providers import ModelConfig, ProviderRole, provider_registry  # noqa: E402
from backend_common.settings import ROOT_DIR, get_settings  # noqa: E402


settings = get_settings()
logger = logging.getLogger(__name__)
CONFIG_DIR = ROOT_DIR / "config"


def provider_unavailable_message(config: ModelConfig) -> str:
    if config.provider_family == "none":
        return "Assistant is disabled because no LLM provider is configured."
    reason = config.availability_reason or f"Provider {config.provider} is not available"
    return f"Model provider '{config.provider}' is not configured: {reason}"


def _assistant_round_limit_message(max_rounds: int) -> str:
    return (
        f"I could not finish the request within {max_rounds} assistant/tool rounds. "
        "Please try again with a narrower request, or ask me to continue from the last completed tool result."
    )


def _ensure_scope_access(
    db: Session,
    context: AuthContext,
    *,
    scope: str,
    workspace_id: str,
    case_id: str | None,
) -> tuple[Workspace, Case | None]:
    """Validate that the current user can use the assistant in the requested scope.

    The assistant can operate at two authorization scopes:

    - ``workspace``: the assistant may discuss and inspect workspace-level state,
      so the user only needs membership in ``workspace_id``.
    - ``case``: the assistant may discuss or use tools against one case, so the
      request must include ``case_id`` and that case must be readable by the user
      inside the requested workspace.

    This helper is the assistant runtime's local scope gate. It first resolves
    the workspace with ``get_workspace_for_user`` so unsupported, missing, or
    unauthorized workspaces are rejected before any assistant conversation state is
    loaded. For case scope, it then resolves the case with ``get_case_for_user``
    constrained to the same workspace, preventing a request from mixing a valid
    workspace with a case from another workspace.

    Parameters
    ----------
    db
        SQLAlchemy session used for workspace/case membership lookups.
    context
        Authenticated user context for the current request.
    scope
        Assistant scope value, normally ``AssistantScope.workspace.value`` or
        ``AssistantScope.case.value``.
    workspace_id
        Workspace that bounds the assistant request and any persisted thread.
    case_id
        Case identifier required only when ``scope`` is ``case``.

    Returns
    -------
    tuple[Workspace, Case | None]
        The authorized workspace and, for case scope, the authorized case. The
        case element is ``None`` for workspace scope.

    Raises
    ------
    HTTPException
        Propagates not-found/permission errors from the workspace and case
        lookup helpers, raises ``400`` for unsupported assistant scopes, and
        raises ``400`` when case scope omits ``case_id``.
    """
    workspace, _workspace_role = get_workspace_for_user(db, workspace_id, context.user.id)
    if scope == AssistantScope.workspace.value:
        return workspace, None
    if scope != AssistantScope.case.value:
        raise HTTPException(status_code=400, detail=f"Unsupported assistant scope: {scope}")
    if not case_id:
        raise HTTPException(status_code=400, detail="Case scope requires case_id")
    case, _case_role = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace.id)
    return workspace, case


class AssistantRuntime:
    def __init__(self, runtime_service: RuntimeService) -> None:
        self.runtime_service = runtime_service
        self.tools = AssistantToolBuilder(runtime_service, settings=settings, root_dir=ROOT_DIR)
        self.loop = AssistantLoop(self.tools, config_dir=CONFIG_DIR)
        self.conversations = AssistantConversationStore()

    async def list_history(
        self,
        db: Session,
        context: AuthContext,
        *,
        scope: str,
        workspace_id: str,
        case_id: str | None = None,
    ) -> list[ChatMessageSummary]:
        _ensure_scope_access(db, context, scope=scope, workspace_id=workspace_id, case_id=case_id)
        return self.conversations.list_history(db, scope=scope, workspace_id=workspace_id, case_id=case_id)

    async def clear_history(
        self,
        db: Session,
        context: AuthContext,
        *,
        scope: str,
        workspace_id: str,
        case_id: str | None = None,
    ) -> None:
        _ensure_scope_access(db, context, scope=scope, workspace_id=workspace_id, case_id=case_id)
        self.conversations.clear_history(db, scope=scope, workspace_id=workspace_id, case_id=case_id)

    async def get_thread_key(
        self,
        db: Session,
        context: AuthContext,
        *,
        scope: str,
        workspace_id: str,
        case_id: str | None = None,
    ) -> str | None:
        _ensure_scope_access(db, context, scope=scope, workspace_id=workspace_id, case_id=case_id)
        return self.conversations.thread_key(db, scope=scope, workspace_id=workspace_id, case_id=case_id)

    async def run_chat(
        self,
        *,
        db: Session | None,
        context: AuthContext | None,
        messages: list[dict[str, Any]],
        workspace_id: str | None,
        case_id: str | None,
        scope: str,
        provider: str | None,
        model: str | None,
        gui_session_id: str | None = None,
        gui_state_override: dict[str, Any] | None = None,
        event_sink: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        diagnostic_request_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        provider_config = provider_registry.get(ProviderRole.chat, provider_override=provider, model_override=model)
        if not provider_config.available:
            raise HTTPException(status_code=502, detail=provider_unavailable_message(provider_config))
        if persist and db is not None and context is not None and workspace_id is not None:
            _ensure_scope_access(db, context, scope=scope, workspace_id=workspace_id, case_id=case_id)

        thread, conversation, latest_messages = self.conversations.prepare_chat(
            db,
            context,
            persist=persist,
            scope=scope,
            workspace_id=workspace_id,
            case_id=case_id,
            provider_config=provider_config,
            messages=messages,
        )
        max_rounds = max(int(settings.assistant_max_rounds), 1)
        state: dict[str, Any] = {
            "db": db,
            "context": context,
            "scope": scope,
            "workspace_id": workspace_id,
            "case_id": case_id,
            "gui_session_id": gui_session_id,
            "gui_state_override": dict(gui_state_override or {}),
            "provider_config": provider_config,
            "event_sink": event_sink,
            "diagnostic_request_id": diagnostic_request_id,
            "conversation": conversation,
            "round_count": 0,
            "max_rounds": max_rounds,
        }
        logger.info(
            "assistant.runtime.started request_id=%s mode=chat scope=%s workspace_id=%s case_id=%s provider=%s model=%s persist=%s",
            diagnostic_request_id,
            scope,
            workspace_id,
            case_id,
            provider_config.provider,
            provider_config.model,
            persist,
        )
        started_at = time.monotonic()
        final_state = await self.loop.run(state)
        logger.info(
            "assistant.runtime.finished request_id=%s elapsed_ms=%s status=%s error=%s tool_call_count=%s",
            diagnostic_request_id,
            int((time.monotonic() - started_at) * 1000),
            final_state.get("status"),
            bool(final_state.get("error")),
            len(final_state.get("tool_calls_log", []) or []),
        )
        if final_state.get("error"):
            detail = (
                _assistant_round_limit_message(max_rounds)
                if "without finishing" in str(final_state["error"])
                else final_state["error"]
            )
            self.conversations.log_failed_turn(
                context,
                thread,
                incoming_messages=latest_messages,
                final_state=final_state,
                error=str(detail),
                diagnostic_request_id=diagnostic_request_id,
            )
            raise HTTPException(status_code=502, detail=detail)

        final_text = final_state.get("final_response") or ""
        if persist and db is not None and context is not None and thread is not None:
            self.conversations.persist_success(
                db,
                context,
                thread,
                incoming_messages=latest_messages,
                final_state=final_state,
                final_text=final_text,
            )
        return done_payload(final_text, final_state.get("tool_calls_log", []))

assistant_runtime = AssistantRuntime(runtime_service)
