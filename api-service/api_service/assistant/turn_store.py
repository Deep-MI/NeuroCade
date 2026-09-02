"""Durable assistant turn and one-time approval lifecycle."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from api_service.assistant.tool_execution_store import approval_digest
from backend_common.auth import AuthContext
from backend_common.db import AssistantThread, AssistantToolExecution, AssistantTurn

logger = logging.getLogger(__name__)
SQLITE_STORAGE_RECOVERY_DELAYS_SECONDS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


def reconcile_interrupted_turns(
    db: Session,
    *,
    tool_recoveries: list[dict[str, Any]] | None = None,
) -> int:
    """Fail closed any model/tool turn left running by a process restart."""
    recoveries_by_turn: dict[str, list[dict[str, Any]]] = {}
    for recovery in tool_recoveries or []:
        recoveries_by_turn.setdefault(str(recovery["turn_id"]), []).append(recovery)
    interrupted = db.query(AssistantTurn).filter(AssistantTurn.status == "running").all()
    for turn in interrupted:
        result = dict(turn.result_json or {})
        result.pop("checkpoint", None)
        result["phase"] = "interrupted"
        if turn.id in recoveries_by_turn:
            result["tool_recoveries"] = recoveries_by_turn[turn.id]
        turn.result_json = result
        turn.status = "failed"
        turn.error_message = "Assistant turn was interrupted by a backend restart"
    if interrupted:
        db.commit()
    return len(interrupted)


class AssistantTurnStore:
    @staticmethod
    def _turn_id(turn: AssistantTurn | str | None) -> str | None:
        if turn is None or isinstance(turn, str):
            return turn
        return str(turn.id)

    def start(
        self,
        db: Session,
        context: AuthContext,
        thread: AssistantThread,
        *,
        request_id: str | None,
        message_count: int,
    ) -> AssistantTurn:
        turn = AssistantTurn(
            id=request_id or str(uuid4()),
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            case_id=thread.case_id,
            user_id=context.user.id,
            status="running",
            request_json={"message_count": message_count},
            result_json={},
        )
        db.add(turn)
        db.commit()
        return turn

    def finish(
        self,
        db: Session,
        turn: AssistantTurn | str | None,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        turn_id = self._turn_id(turn)
        if turn_id is None:
            return
        current = db.get(AssistantTurn, turn_id)
        if current is None:
            return
        current.status = status
        merged_result = dict(current.result_json or {})
        merged_result.update(result or {})
        if status in {"completed", "failed", "canceled"}:
            merged_result.pop("checkpoint", None)
        current.result_json = merged_result
        current.error_message = error
        db.commit()

    def checkpoint(
        self,
        db: Session,
        turn: AssistantTurn | str | None,
        *,
        phase: str,
        state: dict[str, Any],
    ) -> None:
        """Persist the complete safe continuation point for a logical turn."""
        turn_id = self._turn_id(turn)
        if turn_id is None:
            return
        current = db.get(AssistantTurn, turn_id)
        if current is None:
            return
        result = dict(current.result_json or {})
        result["phase"] = phase
        result["checkpoint"] = state
        current.result_json = result
        db.commit()

    def recover_after_storage_error(
        self,
        db: Session,
        turn: AssistantTurn | str | None,
        *,
        error: str,
    ) -> bool:
        """Reconnect, verify integrity, and fail a turn after transient SQLite I/O loss."""
        turn_id = self._turn_id(turn)
        if turn_id is None:
            return False
        bind = db.get_bind()
        db.invalidate()
        dispose = getattr(bind, "dispose", None)
        if callable(dispose):
            dispose()
        recovery_session = sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)
        for delay in SQLITE_STORAGE_RECOVERY_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                with recovery_session() as recovery_db:
                    integrity = recovery_db.execute(text("PRAGMA quick_check")).scalar_one()
                    if integrity != "ok":
                        logger.critical("SQLite integrity check failed during recovery: %s", integrity)
                        return False
                    current = recovery_db.get(AssistantTurn, turn_id)
                    if current is None:
                        return False
                    result = dict(current.result_json or {})
                    result.pop("checkpoint", None)
                    result["phase"] = "storage_error"
                    current.result_json = result
                    current.status = "failed"
                    current.error_message = error
                    recovery_db.commit()
                    return True
            except DBAPIError as recovery_error:
                logger.warning(
                    "SQLite storage recovery attempt failed turn_id=%s error=%s",
                    turn_id,
                    recovery_error,
                )
        return False

    def consume_approvals(
        self,
        db: Session,
        thread: AssistantThread,
        approvals: list[dict[str, Any]],
    ) -> tuple[AssistantTurn, list[dict[str, Any]], dict[str, Any]]:
        waiting = (
            db.query(AssistantTurn)
            .filter(AssistantTurn.thread_id == thread.id, AssistantTurn.status == "awaiting_approval")
            .order_by(AssistantTurn.created_at.desc())
            .all()
        )
        accepted: list[dict[str, Any]] = []
        matched_turn: AssistantTurn | None = None
        for approval in approvals:
            match = waiting[0] if waiting else None
            execution_id = (match.result_json or {}).get("approval_execution_id") if match is not None else None
            execution = db.get(AssistantToolExecution, execution_id) if execution_id else None
            expected_digest = (
                approval_digest(execution.tool_name, execution.arguments_json)
                if execution is not None
                else None
            )
            if (
                match is None
                or execution is None
                or execution.turn_id != match.id
                or execution.status != "planned"
                or approval.get("execution_id") != execution.id
                or approval.get("call_id") != execution.call_id
                or approval.get("name") != execution.tool_name
                or approval.get("arguments") != execution.arguments_json
                or approval.get("digest") != expected_digest
            ):
                match = None
            if match is None:
                raise HTTPException(status_code=400, detail="Assistant tool approval is invalid, expired, or already used")
            if matched_turn is not None and matched_turn.id != match.id:
                raise HTTPException(status_code=400, detail="Tool approvals must belong to one assistant turn")
            matched_turn = match
            match.status = "running"
            waiting.pop(0)
            accepted.append(approval)
        if matched_turn is None:
            raise HTTPException(status_code=400, detail="No assistant approval was supplied")
        checkpoint = dict((matched_turn.result_json or {}).get("checkpoint") or {})
        if not checkpoint:
            raise HTTPException(status_code=409, detail="Assistant approval checkpoint is unavailable")
        db.commit()
        return matched_turn, accepted, checkpoint
