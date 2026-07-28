"""Assistant conversation persistence and diagnostic logging.

This module keeps assistant chat history in the application database and writes
append-only JSONL diagnostics for successful and failed turns. It is responsible
for thread identity, message sequencing, history hydration, persistence of tool
and reasoning metadata, and response payload shaping for the chat API.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.schemas import ChatMessageSummary, ChatToolCallEntry, ReasoningEntry
from backend_common.auth import AuthContext
from backend_common.concurrency import lock_assistant_thread_for_update
from backend_common.db import AssistantCheckpoint, AssistantMessage, AssistantScope, AssistantThread, run_with_sqlite_lock_retry
from backend_common.providers import ModelConfig
from backend_common.settings import ROOT_DIR

logger = logging.getLogger(__name__)
_conversation_log_lock = threading.Lock()
_DEFAULT_CONVERSATION_LOG = ROOT_DIR / ".runtime" / "logs" / "assistant-conversations.jsonl"


def serialize_content(content: Any) -> dict[str, Any]:
    """Wrap arbitrary message content in the database content shape."""
    return {"value": content}


def _conversation_log_path() -> str:
    """Return the configured assistant conversation JSONL log path."""
    return os.environ.get("NEUROCADE_ASSISTANT_CONVERSATION_LOG") or str(_DEFAULT_CONVERSATION_LOG)


def _json_default(value: Any) -> str:
    """Serialize diagnostic log values that the default JSON encoder rejects."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def append_conversation_log(record: dict[str, Any]) -> None:
    """Append one diagnostic record to the assistant conversation JSONL log.

    Logging failures are intentionally swallowed after a warning so diagnostic
    I/O cannot break database persistence or assistant responses.
    """
    path = _conversation_log_path()
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        line = json.dumps(record, default=_json_default, ensure_ascii=False, sort_keys=True)
        with _conversation_log_lock, open(path, "a", encoding="utf-8") as file:
            file.write(f"{line}\n")
    except Exception as exc:  # pragma: no cover - diagnostics must not break chat persistence
        logger.warning("Failed to append assistant conversation log %s: %s", path, exc)


def thread_key(*, scope: str, workspace_id: str, case_id: str | None) -> str:
    """Build the stable assistant thread key for a workspace or case scope."""
    if scope == AssistantScope.workspace.value:
        return f"workspace:{workspace_id}"
    if not case_id:
        raise HTTPException(status_code=400, detail="Case scope requires case_id")
    return f"case:{case_id}"


def find_thread(db: Session, *, scope: str, workspace_id: str, case_id: str | None) -> AssistantThread | None:
    """Return the existing assistant thread for the requested scope, if any."""
    key = thread_key(scope=scope, workspace_id=workspace_id, case_id=case_id)
    return db.query(AssistantThread).filter(AssistantThread.thread_key == key).one_or_none()


def get_or_create_thread(
    db: Session,
    context: AuthContext,
    *,
    scope: str,
    workspace_id: str,
    case_id: str | None,
    provider_name: str,
    model_name: str,
) -> AssistantThread:
    """Return a persisted assistant thread, creating it when necessary.

    Existing threads are updated with the latest provider and model names. A
    duplicate insert race is handled by rolling back, loading the winning row,
    and updating its provider metadata.
    """
    key = thread_key(scope=scope, workspace_id=workspace_id, case_id=case_id)
    thread = db.query(AssistantThread).filter(AssistantThread.thread_key == key).one_or_none()
    if thread is None:
        thread = AssistantThread(
            thread_key=key,
            scope_type=AssistantScope(scope),
            workspace_id=workspace_id,
            case_id=case_id,
            created_by_user_id=context.user.id,
            provider_name=provider_name,
            model_name=model_name,
        )
        db.add(thread)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            thread = db.query(AssistantThread).filter(AssistantThread.thread_key == key).one()
            thread.provider_name = provider_name
            thread.model_name = model_name
            db.flush()
            return thread
    else:
        thread.provider_name = provider_name
        thread.model_name = model_name
    db.flush()
    return thread


def load_thread_history(db: Session, thread_id: str) -> list[dict[str, Any]]:
    """Load prior user and assistant messages for a persisted thread."""
    rows = (
        db.query(AssistantMessage)
        .filter(AssistantMessage.thread_id == thread_id)
        .order_by(AssistantMessage.sequence.asc(), AssistantMessage.created_at.asc())
        .all()
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        if row.role in {"user", "assistant"}:
            history.append({"role": row.role, "content": row.content_json.get("value", "")})
    return history


def persist_turn(
    db: Session,
    context: AuthContext,
    thread: AssistantThread,
    *,
    incoming_messages: list[dict[str, Any]],
    assistant_messages: list[str] | None = None,
    tool_calls_log: list[dict[str, Any]],
    reasoning_entries: list[dict[str, Any]],
    assistant_content: str,
) -> None:
    """Persist a completed turn, retrying the complete SQLite transaction."""
    thread_id = thread.id

    def operation() -> None:
        current_thread = db.get(AssistantThread, thread_id)
        if current_thread is None:
            raise ValueError(f"Assistant thread {thread_id} no longer exists")
        _persist_turn_once(
            db,
            context,
            current_thread,
            incoming_messages=incoming_messages,
            assistant_messages=assistant_messages,
            tool_calls_log=tool_calls_log,
            reasoning_entries=reasoning_entries,
            assistant_content=assistant_content,
        )

    run_with_sqlite_lock_retry(db, operation)


def _persist_turn_once(
    db: Session,
    context: AuthContext,
    thread: AssistantThread,
    *,
    incoming_messages: list[dict[str, Any]],
    assistant_messages: list[str] | None = None,
    tool_calls_log: list[dict[str, Any]],
    reasoning_entries: list[dict[str, Any]],
    assistant_content: str,
) -> None:
    """Persist one completed assistant turn into the thread history.

    The turn is written as incoming user messages, optional interim assistant
    messages, an optional tool/reasoning summary row, and the final assistant
    message. Message sequence assignment is protected by a row lock, and a
    single retry handles concurrent sequence conflicts.
    """
    for attempt in range(2):
        try:
            thread = lock_assistant_thread_for_update(db, thread)
            next_sequence = (
                db.query(AssistantMessage.sequence)
                .filter(AssistantMessage.thread_id == thread.id)
                .order_by(AssistantMessage.sequence.desc())
                .limit(1)
                .scalar()
            )
            sequence = (next_sequence or 0) + 1
            log_messages: list[dict[str, Any]] = []

            for message in incoming_messages:
                role = message.get("role", "user")
                content_json = serialize_content(message.get("content"))
                db.add(
                    AssistantMessage(
                        thread_id=thread.id,
                        workspace_id=thread.workspace_id,
                        case_id=thread.case_id,
                        created_by_user_id=context.user.id,
                        role=role,
                        sequence=sequence,
                        content_json=content_json,
                        metadata_json={},
                    )
                )
                log_messages.append(
                    {
                        "sequence": sequence,
                        "role": role,
                        "content_json": content_json,
                        "metadata_json": {},
                    }
                )
                sequence += 1

            for assistant_message in assistant_messages or []:
                content = str(assistant_message or "").strip()
                if not content:
                    continue
                content_json = serialize_content(content)
                db.add(
                    AssistantMessage(
                        thread_id=thread.id,
                        workspace_id=thread.workspace_id,
                        case_id=thread.case_id,
                        created_by_user_id=context.user.id,
                        role="assistant",
                        sequence=sequence,
                        content_json=content_json,
                        metadata_json={"interim": True},
                    )
                )
                log_messages.append(
                    {
                        "sequence": sequence,
                        "role": "assistant",
                        "content_json": content_json,
                        "metadata_json": {"interim": True},
                    }
                )
                sequence += 1

            if tool_calls_log or reasoning_entries:
                content_json = serialize_content(f"Used {len(tool_calls_log)} tool{'s' if len(tool_calls_log) != 1 else ''}")
                metadata_json = {"toolCalls": tool_calls_log, "reasoningEntries": reasoning_entries}
                db.add(
                    AssistantMessage(
                        thread_id=thread.id,
                        workspace_id=thread.workspace_id,
                        case_id=thread.case_id,
                        created_by_user_id=context.user.id,
                        role="tool-calls",
                        sequence=sequence,
                        content_json=content_json,
                        metadata_json=metadata_json,
                    )
                )
                log_messages.append(
                    {
                        "sequence": sequence,
                        "role": "tool-calls",
                        "content_json": content_json,
                        "metadata_json": metadata_json,
                    }
                )
                sequence += 1

            content_json = serialize_content(assistant_content)
            db.add(
                AssistantMessage(
                    thread_id=thread.id,
                    workspace_id=thread.workspace_id,
                    case_id=thread.case_id,
                    created_by_user_id=context.user.id,
                    role="assistant",
                    sequence=sequence,
                    content_json=content_json,
                    metadata_json={},
                )
            )
            log_messages.append(
                {
                    "sequence": sequence,
                    "role": "assistant",
                    "content_json": content_json,
                    "metadata_json": {},
                }
            )
            db.commit()
            append_conversation_log(
                {
                    "event": "assistant.turn.persisted",
                    "logged_at": datetime.now(UTC).isoformat(),
                    "thread_id": thread.id,
                    "thread_key": thread.thread_key,
                    "scope_type": thread.scope_type.value if isinstance(thread.scope_type, AssistantScope) else str(thread.scope_type),
                    "workspace_id": thread.workspace_id,
                    "case_id": thread.case_id,
                    "created_by_user_id": context.user.id,
                    "provider_name": thread.provider_name,
                    "model_name": thread.model_name,
                    "messages": log_messages,
                }
            )
            return
        except IntegrityError:
            db.rollback()
            if attempt == 0:
                refreshed_thread = db.get(AssistantThread, thread.id)
                if refreshed_thread is not None:
                    thread = refreshed_thread
                continue
            raise


def serialize_history_row(row: AssistantMessage) -> ChatMessageSummary:
    """Convert a stored assistant message row into the API history schema."""
    return ChatMessageSummary(
        role=row.role,
        content=row.content_json.get("value", ""),
        toolCalls=[ChatToolCallEntry(**entry) for entry in row.metadata_json.get("toolCalls", [])],
        reasoningEntries=[ReasoningEntry(**entry) for entry in row.metadata_json.get("reasoningEntries", [])],
    )


class AssistantConversationStore:
    """Facade for assistant history, persistence, and diagnostic turn logging."""

    def list_history(self, db: Session, *, scope: str, workspace_id: str, case_id: str | None = None) -> list[ChatMessageSummary]:
        """Return all stored messages for the requested assistant thread."""
        thread = find_thread(db, scope=scope, workspace_id=workspace_id, case_id=case_id)
        if thread is None:
            return []
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.thread_id == thread.id)
            .order_by(AssistantMessage.sequence.asc(), AssistantMessage.created_at.asc())
            .all()
        )
        return [serialize_history_row(row) for row in rows]

    def clear_history(self, db: Session, *, scope: str, workspace_id: str, case_id: str | None = None) -> None:
        """Delete stored messages and checkpoints for a thread, if it exists."""
        thread = find_thread(db, scope=scope, workspace_id=workspace_id, case_id=case_id)
        if thread is None:
            return
        thread = lock_assistant_thread_for_update(db, thread)
        db.query(AssistantMessage).filter(AssistantMessage.thread_id == thread.id).delete(synchronize_session=False)
        db.query(AssistantCheckpoint).filter(AssistantCheckpoint.thread_id == thread.id).delete(synchronize_session=False)
        db.commit()

    def thread_key(self, db: Session, *, scope: str, workspace_id: str, case_id: str | None = None) -> str | None:
        """Return the persisted thread key for a scope, or ``None`` if absent."""
        thread = find_thread(db, scope=scope, workspace_id=workspace_id, case_id=case_id)
        return thread.thread_key if thread is not None else None

    def prepare_chat(
        self,
        db: Session | None,
        context: AuthContext | None,
        *,
        persist: bool,
        scope: str,
        workspace_id: str | None,
        case_id: str | None,
        provider_config: ModelConfig,
        messages: list[dict[str, Any]],
    ) -> tuple[AssistantThread | None, list[dict[str, Any]], list[dict[str, Any]]]:
        """Prepare persistence state and conversation context for a chat turn.

        When persistence is enabled and a database context is available, the
        target thread is created or loaded and prior history is prepended for
        single-message requests. Multi-message requests are treated as complete
        caller-provided context. The returned ``latest_messages`` are the new
        incoming messages to persist with the assistant response.
        """
        thread: AssistantThread | None = None
        history: list[dict[str, Any]] = []
        if persist and db is not None and context is not None and workspace_id is not None:
            thread = get_or_create_thread(
                db,
                context,
                scope=scope,
                workspace_id=workspace_id,
                case_id=case_id,
                provider_name=provider_config.provider,
                model_name=provider_config.model,
            )
            history = load_thread_history(db, thread.id)
        conversation = messages if len(messages) > 1 else history + messages
        latest_messages = messages[-1:] if messages else []
        return thread, conversation, latest_messages

    def persist_success(
        self,
        db: Session,
        context: AuthContext,
        thread: AssistantThread,
        *,
        incoming_messages: list[dict[str, Any]],
        final_state: dict[str, Any],
        final_text: str,
    ) -> None:
        """Persist a successful assistant turn from the loop's final state."""
        persist_turn(
            db,
            context,
            thread,
            incoming_messages=incoming_messages,
            assistant_messages=final_state.get("assistant_messages", []),
            tool_calls_log=final_state.get("tool_calls_log", []),
            reasoning_entries=final_state.get("reasoning_entries", []),
            assistant_content=final_text,
        )

    def log_failed_turn(
        self,
        context: AuthContext | None,
        thread: AssistantThread | None,
        *,
        incoming_messages: list[dict[str, Any]],
        final_state: dict[str, Any],
        error: str,
        diagnostic_request_id: str | None,
    ) -> None:
        """Write a failed-turn diagnostic log entry when thread context exists."""
        if context is None or thread is None:
            return
        append_conversation_log(
            {
                "event": "assistant.turn.failed",
                "logged_at": datetime.now(UTC).isoformat(),
                "assistant_request_id": diagnostic_request_id,
                "thread_id": thread.id,
                "thread_key": thread.thread_key,
                "scope_type": thread.scope_type.value if isinstance(thread.scope_type, AssistantScope) else str(thread.scope_type),
                "workspace_id": thread.workspace_id,
                "case_id": thread.case_id,
                "created_by_user_id": context.user.id,
                "provider_name": thread.provider_name,
                "model_name": thread.model_name,
                "error": error,
                "status": final_state.get("status"),
                "incoming_messages": [
                    {
                        "role": message.get("role", "user"),
                        "content_json": serialize_content(message.get("content")),
                    }
                    for message in incoming_messages
                ],
                "pending_tool_calls": final_state.get("pending_tool_calls", []),
                "tool_calls_log": final_state.get("tool_calls_log", []),
                "reasoning_entries": final_state.get("reasoning_entries", []),
                "result": final_state.get("result", {}),
            }
        )


def done_payload(content: str, tool_calls_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the final assistant turn response payload."""
    payload: dict[str, Any] = {
        "message": {
            "role": "assistant",
            "content": content,
        }
    }
    if tool_calls_log:
        payload["tool_calls_log"] = tool_calls_log
    return payload
