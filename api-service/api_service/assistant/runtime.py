"""Assistant runtime coordination."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.conversation_store import AssistantHistoryState, AssistantHistoryStore  # noqa: E402
from api_service.assistant.loop import AssistantLoop  # noqa: E402
from api_service.assistant.tools import AssistantToolBuilder  # noqa: E402
from api_service.assistant.turn_store import AssistantTurnStore  # noqa: E402
from api_service.helpers import get_case_for_user, get_workspace_for_user  # noqa: E402
from api_service.runtime.gui_runtime import GuiRuntime, gui_runtime  # noqa: E402
from api_service.schemas import ChatMessageSummary  # noqa: E402
from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import (  # noqa: E402
    AssistantScope,
    Case,
    Workspace,
    is_sqlite_storage_error,
    run_with_sqlite_lock_retry,
)
from backend_common.providers import ModelConfig, provider_registry  # noqa: E402
from backend_common.settings import ROOT_DIR, get_settings  # noqa: E402

settings = get_settings()
logger = logging.getLogger(__name__)
CONFIG_DIR = ROOT_DIR / "config"


def provider_unavailable_message(config: ModelConfig) -> str:
    if config.provider_family == "none":
        return "Assistant is disabled because no LLM provider is configured."
    reason = config.configuration_reason or f"Provider {config.provider} is not configured"
    return f"Model provider '{config.provider}' is not configured: {reason}"


def _assistant_round_limit_message(max_rounds: int) -> str:
    return (
        f"I could not finish the request within {max_rounds} assistant/tool rounds. "
        "Please try again with a narrower request, or ask me to continue from the last completed tool result."
    )


def _checkpoint_content(content: Any) -> Any:
    """Remove large transient image payloads from durable turn checkpoints."""
    if not isinstance(content, list):
        return content
    return [
        {"type": "text", "text": "[MRI snapshot omitted from durable turn checkpoint.]"}
        if isinstance(part, dict) and part.get("type") == "image_url"
        else part
        for part in content
    ]


def _checkpoint_state(state: dict[str, Any]) -> dict[str, Any]:
    """Select only JSON-safe state required to resume at a tool boundary."""
    return {
        "conversation": [
            (
                {
                    "role": "tool",
                    "name": message.get("name"),
                    "call_id": message.get("call_id"),
                    "ledger_call_id": message.get("call_id"),
                    "content": "",
                }
                if message.get("role") == "tool" and message.get("call_id")
                else {**message, "content": _checkpoint_content(message.get("content"))}
            )
            for message in state.get("conversation", [])
            if isinstance(message, dict)
        ],
        "pending_tool_calls": list(state.get("pending_tool_calls", [])),
        "reasoning_entries": list(state.get("reasoning_entries", [])),
        "assistant_messages": list(state.get("assistant_messages", [])),
        "round_count": int(state.get("round_count", 0)),
        "last_tool_call_fingerprint": state.get("last_tool_call_fingerprint"),
    }


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
    case, _workspace, _case_role, _case_dir = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace.id)
    return workspace, case


class AssistantRuntime:
    def __init__(self, gui_runtime: GuiRuntime) -> None:
        self.tools = AssistantToolBuilder(gui_runtime, settings=settings)
        self.loop = AssistantLoop(self.tools, config_dir=CONFIG_DIR)
        self.history = AssistantHistoryStore()
        self.turns = AssistantTurnStore()

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
        return self.history.list_history(db, user_id=context.user.id, scope=scope, workspace_id=workspace_id, case_id=case_id)

    async def get_history_state(
        self,
        db: Session,
        context: AuthContext,
        *,
        scope: str,
        workspace_id: str,
        case_id: str | None = None,
    ) -> AssistantHistoryState:
        """Return authorized display and approval state for one private thread."""
        _ensure_scope_access(db, context, scope=scope, workspace_id=workspace_id, case_id=case_id)
        return self.history.history_state(
            db,
            user_id=context.user.id,
            scope=scope,
            workspace_id=workspace_id,
            case_id=case_id,
        )

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
        self.history.clear_history(db, user_id=context.user.id, scope=scope, workspace_id=workspace_id, case_id=case_id)

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
        return self.history.thread_key(db, user_id=context.user.id, scope=scope, workspace_id=workspace_id, case_id=case_id)

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
        gui_session_id: str,
        gui_state_override: dict[str, Any] | None = None,
        tool_approvals: list[dict[str, Any]] | None = None,
        event_sink: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        diagnostic_request_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        provider_config = provider_registry.get(provider_override=provider, model_override=model)
        if not provider_config.configured:
            raise HTTPException(status_code=502, detail=provider_unavailable_message(provider_config))
        if persist and db is not None and context is not None and workspace_id is not None:
            _ensure_scope_access(db, context, scope=scope, workspace_id=workspace_id, case_id=case_id)

        def prepare_conversation():
            prepared = self.history.prepare_chat(
                db,
                context,
                persist=persist,
                scope=scope,
                workspace_id=workspace_id,
                case_id=case_id,
                provider_config=provider_config,
                messages=messages,
            )
            if db is not None and db.in_transaction():
                db.commit()
            return prepared

        if db is not None:
            thread, conversation, latest_messages = run_with_sqlite_lock_retry(db, prepare_conversation)
        else:
            thread, conversation, latest_messages = prepare_conversation()
        approved_tools: list[dict[str, Any]] = []
        resume_checkpoint: dict[str, Any] = {}
        turn = None
        if tool_approvals:
            if not persist or db is None or thread is None:
                raise HTTPException(status_code=400, detail="Tool approvals require a persisted private chat")
            turn, approved_tools, resume_checkpoint = self.turns.consume_approvals(db, thread, tool_approvals)
        max_rounds = max(int(settings.assistant_max_rounds), 1)
        state: dict[str, Any] = {
            "db": db,
            "context": context,
            "scope": scope,
            "workspace_id": workspace_id,
            "case_id": case_id,
            "gui_session_id": gui_session_id,
            "gui_state_override": dict(gui_state_override or {}),
            "tool_approvals": list(approved_tools),
            "pending_tool_calls": list(resume_checkpoint.get("pending_tool_calls", [])),
            "provider_config": provider_config,
            "event_sink": event_sink,
            "diagnostic_request_id": diagnostic_request_id,
            "conversation": list(resume_checkpoint.get("conversation", conversation)),
            "round_count": int(resume_checkpoint.get("round_count", 0)),
            "max_rounds": max_rounds,
            "tool_calls_log": [],
            "reasoning_entries": list(resume_checkpoint.get("reasoning_entries", [])),
            "assistant_messages": list(resume_checkpoint.get("assistant_messages", [])),
            "last_tool_call_fingerprint": resume_checkpoint.get("last_tool_call_fingerprint"),
        }
        if turn is None and persist and db is not None and context is not None and thread is not None:
            turn = self.turns.start(
                db,
                context,
                thread,
                request_id=diagnostic_request_id,
                message_count=len(latest_messages),
            )
        state["turn_id"] = turn.id if turn is not None else None
        turn_id = state["turn_id"]
        if resume_checkpoint and turn is not None:
            state["conversation"] = self.loop.executions.hydrate_conversation(
                db,
                turn.id,
                list(state["conversation"]),
            )
            state["tool_calls_log"] = self.loop.executions.logs_for_turn(db, turn.id)
        if persist and db is not None and turn is not None:
            async def checkpoint_sink(phase: str, checkpoint_state: dict[str, Any]) -> None:
                self.turns.checkpoint(
                    db,
                    turn,
                    phase=phase,
                    state=_checkpoint_state(checkpoint_state),
                )

            state["checkpoint_sink"] = checkpoint_sink
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
        try:
            final_state = await self.loop.run(state)
        except asyncio.CancelledError:
            if db is not None:
                db.rollback()
                self.turns.finish(db, turn_id, status="canceled")
            raise
        except Exception as exc:
            if db is not None:
                if is_sqlite_storage_error(exc):
                    recovered = await asyncio.to_thread(
                        self.turns.recover_after_storage_error,
                        db,
                        turn_id,
                        error=str(exc),
                    )
                    if not recovered:
                        logger.critical(
                            "assistant.runtime.storage_recovery_failed request_id=%s turn_id=%s",
                            diagnostic_request_id,
                            turn_id,
                        )
                else:
                    db.rollback()
                    self.turns.finish(db, turn_id, status="failed", error=str(exc))
            raise
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
            if persist and db is not None and context is not None and thread is not None:
                self.history.persist_success(
                    db,
                    context,
                    thread,
                    incoming_messages=latest_messages,
                    final_state=final_state,
                    final_text=f"Assistant turn failed: {detail}",
                )
            if db is not None:
                self.turns.finish(db, turn, status="failed", error=str(detail))
            raise HTTPException(status_code=502, detail=detail)

        final_text = final_state.get("final_response") or ""
        if persist and db is not None and context is not None and thread is not None:
            self.history.persist_success(
                db,
                context,
                thread,
                incoming_messages=latest_messages,
                final_state=final_state,
                final_text=final_text,
            )
            self.turns.finish(
                db,
                turn,
                status=str(final_state.get("status") or "completed"),
                result={
                    "tool_call_count": len(final_state.get("tool_calls_log", []) or []),
                    "approval_execution_id": (
                        (final_state.get("approval_request") or {}).get("execution_id")
                    ),
                    "approval_request": final_state.get("approval_request"),
                },
            )
        return _done_payload(
            final_text,
            final_state.get("tool_calls_log", []),
            approval_request=final_state.get("approval_request"),
            turn_id=turn.id if turn is not None else None,
        )

def _done_payload(
    content: str,
    tool_calls: list[dict[str, Any]],
    *,
    approval_request: dict[str, Any] | None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": {"role": "assistant", "content": content}}
    if turn_id:
        payload["turn_id"] = turn_id
    if tool_calls:
        payload["tool_calls_log"] = tool_calls
    if approval_request:
        payload["approval_request"] = approval_request
    return payload


assistant_runtime = AssistantRuntime(gui_runtime)
