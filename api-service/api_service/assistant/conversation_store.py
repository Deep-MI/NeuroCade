"""Private assistant thread and message history persistence."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.assistant.compaction import (
    build_domain_summary,
    estimate_messages_tokens,
    select_recent_messages,
)
from api_service.schemas import (
    AssistantApprovalRequestResponse,
    ChatMessageSummary,
    ChatToolCallEntry,
    ReasoningEntry,
)
from backend_common.auth import AuthContext
from backend_common.db import AssistantMessage, AssistantScope, AssistantThread, AssistantTurn, run_with_sqlite_lock_retry
from backend_common.providers import ModelConfig
from backend_common.settings import get_settings

settings = get_settings()
_history_write_lock = threading.Lock()
HISTORY_OMISSION_NOTICE = (
    "[History context notice: older private-thread messages were omitted and/or "
    "the oldest retained message was compacted to fit the configured history "
    "budget. The newest history was prioritized.]"
)
CONTEXT_SUMMARY_ROLE = "context-summary"


@dataclass(frozen=True)
class AssistantHistoryState:
    """Displayable state for one private assistant thread."""

    thread_key: str | None
    messages: list[ChatMessageSummary]
    pending_approval: AssistantApprovalRequestResponse | None


def _context_message_size(message: dict[str, Any]) -> int:
    return len(json.dumps(message, default=str, ensure_ascii=False))


def _compact_history_text(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    marker = f" [... omitted {len(content) - limit} characters ...] "
    if limit <= len(marker) + 2:
        return content[:limit]
    available = limit - len(marker)
    head = available // 2
    return content[:head] + marker + content[-(available - head):]


def serialize_content(content: Any) -> dict[str, Any]:
    """Wrap arbitrary message content in the database content shape."""
    return {"value": content}


def thread_key(*, user_id: str, scope: str, workspace_id: str, case_id: str | None) -> str:
    """Build the stable private assistant thread key for one user and scope."""
    if scope == AssistantScope.case.value and not case_id:
        raise HTTPException(status_code=400, detail="Case scope requires case_id")
    if scope not in {AssistantScope.workspace.value, AssistantScope.case.value}:
        raise HTTPException(status_code=400, detail=f"Unsupported assistant scope: {scope}")
    identity = json.dumps(
        {"user_id": user_id, "scope": scope, "workspace_id": workspace_id, "case_id": case_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"private:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def find_thread(db: Session, *, user_id: str, scope: str, workspace_id: str, case_id: str | None) -> AssistantThread | None:
    """Return the existing assistant thread for the requested scope, if any."""
    key = thread_key(user_id=user_id, scope=scope, workspace_id=workspace_id, case_id=case_id)
    return (
        db.query(AssistantThread)
        .filter(AssistantThread.thread_key == key, AssistantThread.created_by_user_id == user_id)
        .one_or_none()
    )


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
    key = thread_key(user_id=context.user.id, scope=scope, workspace_id=workspace_id, case_id=case_id)
    thread = (
        db.query(AssistantThread)
        .filter(AssistantThread.thread_key == key, AssistantThread.created_by_user_id == context.user.id)
        .one_or_none()
    )
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
            thread = (
                db.query(AssistantThread)
                .filter(AssistantThread.thread_key == key, AssistantThread.created_by_user_id == context.user.id)
                .one()
            )
            thread.provider_name = provider_name
            thread.model_name = model_name
            db.flush()
            return thread
    else:
        thread.provider_name = provider_name
        thread.model_name = model_name
    db.flush()
    return thread


def _history_row_message(row: AssistantMessage) -> dict[str, Any] | None:
    """Convert one durable row into provider-neutral conversation data."""
    if row.role == "tool-calls":
        evidence = []
        for entry in row.metadata_json.get("toolCalls", []):
            evidence.append(
                {
                    "role": "tool",
                    "name": entry.get("name", "tool"),
                    "call_id": entry.get("call_id"),
                    "content": entry.get("result", ""),
                }
            )
        if not evidence:
            return None
        return {
            "role": "tool",
            "content": "\n\n".join(
                f"{entry['name']}({json.dumps(call.get('arguments', {}), sort_keys=True, default=str)}): {entry['content']}"
                for entry, call in zip(evidence, row.metadata_json.get("toolCalls", []), strict=False)
            ),
        }
    return {"role": row.role, "content": row.content_json.get("value", "")}


def compact_thread_history(db: Session, thread: AssistantThread) -> None:
    """Persist a token-aware summary covering old complete conversation turns."""
    latest_summary = (
        db.query(AssistantMessage)
        .filter(AssistantMessage.thread_id == thread.id, AssistantMessage.role == CONTEXT_SUMMARY_ROLE)
        .order_by(AssistantMessage.sequence.desc())
        .first()
    )
    covered_sequence = int((latest_summary.metadata_json if latest_summary else {}).get("through_sequence", 0))
    rows = (
        db.query(AssistantMessage)
        .filter(
            AssistantMessage.thread_id == thread.id,
            AssistantMessage.role != CONTEXT_SUMMARY_ROLE,
            AssistantMessage.sequence > covered_sequence,
        )
        .order_by(AssistantMessage.sequence)
        .all()
    )
    messages_with_rows = [
        (message, row)
        for row in rows
        if (message := _history_row_message(row)) is not None
    ]
    messages = [message for message, _row in messages_with_rows]
    previous_summary_tokens = estimate_messages_tokens(
        [{"role": "context", "content": latest_summary.content_json.get("value", "")}]
    ) if latest_summary is not None else 0
    if (
        len(messages) <= settings.assistant_history_max_messages
        and estimate_messages_tokens(messages) + previous_summary_tokens <= settings.assistant_history_max_tokens
    ):
        return

    compacted, _recent = select_recent_messages(
        messages,
        token_budget=min(
            settings.assistant_history_keep_recent_tokens,
            max(settings.assistant_history_max_tokens // 2, 128),
        ),
    )
    if not compacted:
        return
    compacted_count = len(compacted)
    through_sequence = messages_with_rows[compacted_count - 1][1].sequence
    previous_summary = (
        str(latest_summary.content_json.get("value", "")) if latest_summary is not None else None
    )
    summary = build_domain_summary(
        compacted,
        previous_summary=previous_summary,
        max_tokens=max(settings.assistant_history_max_tokens // 4, 256),
    )
    next_sequence = (
        db.query(AssistantMessage.sequence)
        .filter(AssistantMessage.thread_id == thread.id)
        .order_by(AssistantMessage.sequence.desc())
        .limit(1)
        .scalar()
    )
    db.add(
        AssistantMessage(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            case_id=thread.case_id,
            created_by_user_id=thread.created_by_user_id,
            role=CONTEXT_SUMMARY_ROLE,
            sequence=(next_sequence or 0) + 1,
            content_json=serialize_content(summary),
            metadata_json={"through_sequence": through_sequence},
        )
    )
    db.flush()


def load_thread_history(db: Session, thread_id: str) -> list[dict[str, Any]]:
    """Load compacted private history within token and hard character bounds."""
    message_limit = max(settings.assistant_history_max_messages, 1)
    latest_summary = (
        db.query(AssistantMessage)
        .filter(AssistantMessage.thread_id == thread_id, AssistantMessage.role == CONTEXT_SUMMARY_ROLE)
        .order_by(AssistantMessage.sequence.desc())
        .first()
    )
    covered_sequence = int((latest_summary.metadata_json if latest_summary else {}).get("through_sequence", 0))
    rows = (
        db.query(AssistantMessage)
        .filter(
            AssistantMessage.thread_id == thread_id,
            AssistantMessage.role.in_(("user", "assistant", "tool-calls")),
            AssistantMessage.sequence > covered_sequence,
        )
        .order_by(AssistantMessage.sequence.desc(), AssistantMessage.created_at.desc())
        .limit(message_limit + 1)
        .all()
    )
    limited_by_count = len(rows) > message_limit
    rows = rows[:message_limit][::-1]
    candidates = [message for row in rows if (message := _history_row_message(row)) is not None]
    summary_message = (
        {"role": "context", "content": latest_summary.content_json.get("value", "")}
        if latest_summary is not None
        else None
    )
    token_limit = max(settings.assistant_history_max_tokens, 1)
    summary_tokens = estimate_messages_tokens([summary_message]) if summary_message else 0
    if estimate_messages_tokens(candidates) + summary_tokens > token_limit:
        _compacted, candidates = select_recent_messages(
            candidates,
            token_budget=max(token_limit - summary_tokens, 1),
        )
        limited_by_count = True
    if summary_message is not None:
        candidates.insert(0, summary_message)
    character_limit = max(settings.assistant_history_max_characters, 1)
    candidate_size = sum(_context_message_size(message) for message in candidates)
    needs_notice = limited_by_count or candidate_size > character_limit
    notice = {"role": "context", "content": HISTORY_OMISSION_NOTICE}
    remaining_characters = max(
        character_limit - (_context_message_size(notice) if needs_notice else 0),
        0,
    )
    history: list[dict[str, Any]] = []
    for message in reversed(candidates):
        message_size = _context_message_size(message)
        if message_size > remaining_characters:
            content = message.get("content")
            empty_message = {**message, "content": ""}
            content_limit = remaining_characters - _context_message_size(empty_message)
            if not history and isinstance(content, str) and content_limit > 0:
                history.append({**message, "content": _compact_history_text(content, content_limit)})
            needs_notice = True
            break
        history.append(message)
        remaining_characters -= message_size
    history.reverse()
    if needs_notice:
        history.insert(0, notice)
    return history


def _content_for_persistence(content: Any) -> Any:
    """Keep text while replacing large inline image data with a stable marker."""
    if not isinstance(content, list):
        return content
    sanitized: list[Any] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            sanitized.append(
                {
                    "type": "text",
                    "text": "[MRI snapshot was available for this turn but is not retained.]",
                }
            )
        else:
            sanitized.append(part)
    return sanitized


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

    # NeuroCade is a single-process monolith. Serialize the short sequence
    # allocation transaction so concurrent turns cannot choose the same value.
    with _history_write_lock:
        run_with_sqlite_lock_retry(db, operation)


def persist_incoming_messages(
    db: Session,
    context: AuthContext,
    thread: AssistantThread,
    *,
    incoming_messages: list[dict[str, Any]],
) -> None:
    """Persist accepted user input before a potentially long assistant turn."""
    if not incoming_messages:
        return
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
            tool_calls_log=[],
            reasoning_entries=[],
            assistant_content=None,
        )

    # Saving the prompt separately makes an in-flight turn reconstructable after
    # the chat component unmounts or the browser discards a background tab.
    with _history_write_lock:
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
    assistant_content: str | None,
) -> None:
    """Persist one completed assistant turn into the thread history.

    The turn is written as incoming user messages, optional interim assistant
    messages, an optional tool/reasoning summary row, and the final assistant
    message. A single retry handles concurrent message-sequence conflicts.
    """
    for attempt in range(2):
        try:
            next_sequence = (
                db.query(AssistantMessage.sequence)
                .filter(AssistantMessage.thread_id == thread.id)
                .order_by(AssistantMessage.sequence.desc())
                .limit(1)
                .scalar()
            )
            sequence = (next_sequence or 0) + 1
            for message in incoming_messages:
                role = message.get("role", "user")
                content_json = serialize_content(_content_for_persistence(message.get("content")))
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
                sequence += 1

            if assistant_content is not None:
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
            db.commit()
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


class AssistantHistoryStore:
    """Persist private assistant threads and displayable message history."""

    def list_history(self, db: Session, *, user_id: str, scope: str, workspace_id: str, case_id: str | None = None) -> list[ChatMessageSummary]:
        """Return all stored messages for the requested assistant thread."""
        thread = find_thread(db, user_id=user_id, scope=scope, workspace_id=workspace_id, case_id=case_id)
        if thread is None:
            return []
        return self._list_thread_messages(db, thread)

    def history_state(
        self,
        db: Session,
        *,
        user_id: str,
        scope: str,
        workspace_id: str,
        case_id: str | None = None,
    ) -> AssistantHistoryState:
        """Return messages and resumable approval state from one thread lookup."""
        thread = find_thread(db, user_id=user_id, scope=scope, workspace_id=workspace_id, case_id=case_id)
        if thread is None:
            return AssistantHistoryState(thread_key=None, messages=[], pending_approval=None)
        awaiting_turn = (
            db.query(AssistantTurn)
            .filter(
                AssistantTurn.thread_id == thread.id,
                AssistantTurn.status == "awaiting_approval",
            )
            .order_by(AssistantTurn.updated_at.desc())
            .first()
        )
        pending_approval_payload = (
            (awaiting_turn.result_json or {}).get("approval_request")
            if awaiting_turn is not None
            else None
        )
        pending_approval = (
            AssistantApprovalRequestResponse.model_validate(pending_approval_payload)
            if pending_approval_payload is not None
            else None
        )
        return AssistantHistoryState(
            thread_key=thread.thread_key,
            messages=self._list_thread_messages(db, thread),
            pending_approval=pending_approval,
        )

    @staticmethod
    def _list_thread_messages(db: Session, thread: AssistantThread) -> list[ChatMessageSummary]:
        """Load bounded display messages for an already resolved thread."""
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.thread_id == thread.id, AssistantMessage.role != CONTEXT_SUMMARY_ROLE)
            .order_by(AssistantMessage.sequence.desc(), AssistantMessage.created_at.desc())
            .limit(max(settings.assistant_history_display_limit, 1))
            .all()[::-1]
        )
        return [serialize_history_row(row) for row in rows]

    def clear_history(self, db: Session, *, user_id: str, scope: str, workspace_id: str, case_id: str | None = None) -> None:
        """Delete stored messages for a thread, if it exists."""
        thread = find_thread(db, user_id=user_id, scope=scope, workspace_id=workspace_id, case_id=case_id)
        if thread is None:
            return
        db.query(AssistantMessage).filter(AssistantMessage.thread_id == thread.id).delete(synchronize_session=False)
        db.query(AssistantTurn).filter(AssistantTurn.thread_id == thread.id).delete(synchronize_session=False)
        db.commit()

    def thread_key(self, db: Session, *, user_id: str, scope: str, workspace_id: str, case_id: str | None = None) -> str | None:
        """Return the persisted thread key for a scope, or ``None`` if absent."""
        thread = find_thread(db, user_id=user_id, scope=scope, workspace_id=workspace_id, case_id=case_id)
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
        target thread is created or loaded and its bounded prior history is
        prepended to the new caller turn. The returned ``latest_messages`` are
        the new incoming messages to persist with the assistant response.
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
            compact_thread_history(db, thread)
            history = load_thread_history(db, thread.id)
        conversation = history + messages if thread is not None else messages
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

    def persist_incoming(
        self,
        db: Session,
        context: AuthContext,
        thread: AssistantThread,
        *,
        incoming_messages: list[dict[str, Any]],
    ) -> None:
        """Durably record accepted input independently from turn completion."""
        persist_incoming_messages(
            db,
            context,
            thread,
            incoming_messages=incoming_messages,
        )
