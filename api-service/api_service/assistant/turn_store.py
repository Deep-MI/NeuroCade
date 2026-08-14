"""Durable assistant turn and one-time approval lifecycle."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.tool_execution_store import approval_digest
from backend_common.auth import AuthContext
from backend_common.db import AssistantThread, AssistantToolExecution, AssistantTurn


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
        turn: AssistantTurn | None,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if turn is None:
            return
        current = db.get(AssistantTurn, turn.id)
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
        turn: AssistantTurn | None,
        *,
        phase: str,
        state: dict[str, Any],
    ) -> None:
        """Persist the complete safe continuation point for a logical turn."""
        if turn is None:
            return
        current = db.get(AssistantTurn, turn.id)
        if current is None:
            return
        result = dict(current.result_json or {})
        result["phase"] = phase
        result["checkpoint"] = state
        current.result_json = result
        db.commit()

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
