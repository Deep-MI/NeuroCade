"""Durable assistant tool-call execution and replay semantics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.assistant.tools.definition import ToolDefinition, ToolResult
from backend_common.db import AssistantToolExecution, AssistantTurn, Run, run_with_sqlite_lock_retry

TERMINAL_EXECUTION_STATUSES = {"succeeded", "failed", "ambiguous"}


def arguments_digest(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_digest(tool_name: str, arguments: dict[str, Any]) -> str:
    """Bind a user approval to one exact tool name and argument object."""
    canonical = json.dumps(
        {"name": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def workflow_run_id(turn_id: str, call_id: str) -> str:
    """Derive the stable workflow identity before any submission side effect."""
    return str(uuid5(NAMESPACE_URL, f"neurocade:assistant-tool:{turn_id}:{call_id}"))


class AssistantToolExecutionStore:
    @staticmethod
    def logs_for_turn(db: Session | None, turn_id: str | None) -> list[dict[str, Any]]:
        """Project durable terminal ledger rows into the API/history log shape."""
        if db is None or turn_id is None:
            return []
        rows = (
            db.query(AssistantToolExecution)
            .filter(
                AssistantToolExecution.turn_id == turn_id,
                AssistantToolExecution.status.in_(TERMINAL_EXECUTION_STATUSES),
            )
            .order_by(AssistantToolExecution.created_at, AssistantToolExecution.id)
            .all()
        )
        logs = []
        for row in rows:
            result = ToolResult.from_dict(dict(row.result_json or {}))
            logs.append(
                {
                    "call_id": row.call_id,
                    "execution_id": row.id,
                    "ledger_status": row.status,
                    "external_run_id": row.external_run_id,
                    "name": row.tool_name,
                    "arguments": dict(row.arguments_json or {}),
                    "result": result.content,
                    "is_error": result.is_error,
                    "details": result.details,
                    "artifacts": result.artifacts,
                    "terminal": result.terminal,
                    "elapsed_ms": None,
                }
            )
        return logs

    @classmethod
    def hydrate_conversation(
        cls,
        db: Session | None,
        turn_id: str | None,
        conversation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve checkpoint tool references from the authoritative ledger."""
        if db is None or turn_id is None:
            return conversation
        logs = {entry["call_id"]: entry for entry in cls.logs_for_turn(db, turn_id)}
        hydrated = []
        for message in conversation:
            if message.get("role") != "tool":
                hydrated.append(message)
                continue
            ledger_call_id = message.get("ledger_call_id")
            if not ledger_call_id:
                hydrated.append(message)
                continue
            call_id = ledger_call_id
            entry = logs.get(call_id)
            if entry is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Assistant checkpoint references unavailable tool call {call_id!r}",
                )
            hydrated.append(
                {
                    "role": "tool",
                    "name": entry["name"],
                    "call_id": entry["call_id"],
                    "content": f"{entry['name']}: {entry['result']}",
                }
            )
        return hydrated

    def plan(
        self,
        db: Session | None,
        *,
        turn_id: str | None,
        tool: ToolDefinition,
        call_id: str,
        arguments: dict[str, Any],
        approved: bool,
    ) -> AssistantToolExecution | None:
        if db is None or turn_id is None:
            return None
        turn = db.get(AssistantTurn, turn_id)
        if turn is None:
            raise HTTPException(status_code=409, detail="Assistant tool turn is no longer available")
        digest = arguments_digest(arguments)
        execution = (
            db.query(AssistantToolExecution)
            .filter(
                AssistantToolExecution.turn_id == turn_id,
                AssistantToolExecution.call_id == call_id,
            )
            .one_or_none()
        )
        if execution is not None:
            if execution.tool_name != tool.name or execution.arguments_digest != digest:
                raise HTTPException(
                    status_code=409,
                    detail="Assistant tool call ID was reused with different arguments",
                )
            return execution
        execution = AssistantToolExecution(
            turn_id=turn.id,
            thread_id=turn.thread_id,
            workspace_id=turn.workspace_id,
            case_id=turn.case_id,
            user_id=turn.user_id,
            call_id=call_id,
            tool_name=tool.name,
            arguments_digest=digest,
            arguments_json=arguments,
            risk=tool.risk.value,
            status="approved" if approved else "planned",
            result_json={},
            external_run_id=workflow_run_id(turn.id, call_id) if tool.creates_run or tool.name == "tool_call" else None,
        )
        db.add(execution)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            execution = (
                db.query(AssistantToolExecution)
                .filter(
                    AssistantToolExecution.turn_id == turn_id,
                    AssistantToolExecution.call_id == call_id,
                )
                .one_or_none()
            )
            if execution is None:
                raise
            if execution.tool_name != tool.name or execution.arguments_digest != digest:
                raise HTTPException(
                    status_code=409,
                    detail="Assistant tool call ID was reused with different arguments",
                ) from exc
        return execution

    @staticmethod
    def approve(db: Session | None, execution: AssistantToolExecution | None) -> None:
        if db is None or execution is None or execution.status != "planned":
            return
        execution.status = "approved"
        db.commit()

    @staticmethod
    def begin(
        db: Session | None,
        execution: AssistantToolExecution | None,
        *,
        approve_planned: bool = False,
    ) -> ToolResult | None:
        """Enter the execution window or return a prior terminal result."""
        if db is None or execution is None:
            return None
        if execution.status in {"succeeded", "failed"}:
            result = ToolResult.from_dict(dict(execution.result_json or {}))
            return replace(result, details={**result.details, "ledger_replay": True})
        if execution.status == "ambiguous":
            return ToolResult.from_dict(dict(execution.result_json or {}))
        if execution.status == "executing":
            return ToolResult(
                content=(
                    "Error: This tool call has an unresolved prior execution. "
                    "NeuroCade will not execute it again automatically."
                ),
                is_error=True,
                details={"ledger_status": "ambiguous"},
                terminal=True,
            )
        expected_status = "planned" if approve_planned else "approved"
        if execution.status != expected_status:
            raise HTTPException(status_code=409, detail="Tool call has not been approved")
        execution_id = execution.id

        def claim():
            db.rollback()
            db.connection(execution_options={"sqlite_begin_immediate": True})
            claimed = db.query(AssistantToolExecution).filter(
                AssistantToolExecution.id == execution_id,
                AssistantToolExecution.status == expected_status,
            ).update({"status": "executing"}, synchronize_session=False)
            db.commit()
            return claimed

        claimed = run_with_sqlite_lock_retry(db, claim)
        db.refresh(execution)
        if not claimed:
            return ToolResult.error("Invocation already claimed", details={"ledger_status": execution.status})
        return None

    @staticmethod
    def complete(
        db: Session | None,
        execution: AssistantToolExecution | None,
        result: ToolResult,
        *,
        retain_arguments: bool = True,
    ) -> None:
        if db is None or execution is None:
            return
        identity = inspect(execution).identity
        if identity is None:
            raise ValueError("Completion requires a persisted invocation")
        execution_id = identity[0]
        values: dict[Any, Any] = {
            "status": "failed" if result.is_error else "succeeded",
            "result_json": result.as_dict(),
            "error_message": result.content if result.is_error else None,
        }
        if not retain_arguments:
            values["arguments_json"] = {}

        def persist_completion():
            # The handler has finished; only this bookkeeping transaction retries.
            # Drop any read snapshot left by preparation or a concurrent worker.
            db.rollback()
            db.connection(execution_options={"sqlite_begin_immediate": True})
            db.query(AssistantToolExecution).filter(
                AssistantToolExecution.id == execution_id,
                AssistantToolExecution.status == "executing",
            ).update(values, synchronize_session=False)
            db.commit()

        run_with_sqlite_lock_retry(db, persist_completion)
        db.refresh(execution)

    @staticmethod
    def interrupt(
        db: Session | None,
        execution: AssistantToolExecution | None,
        *,
        reason: str,
    ) -> None:
        """Fail closed an execution whose side-effect outcome cannot be proven."""
        if db is None or execution is None:
            return
        current = db.get(AssistantToolExecution, execution.id)
        if current is None or current.status != "executing":
            return
        result = ToolResult(
            content=(
                f"Error: {reason} The tool was not retried because its side-effect outcome is unknown."
            ),
            is_error=True,
            details={"ledger_status": "ambiguous"},
            terminal=True,
        )
        current.status = "ambiguous"
        current.result_json = result.as_dict()
        current.error_message = result.content
        db.commit()


def reconcile_interrupted_tool_executions(db: Session) -> list[dict[str, Any]]:
    """Resolve tool calls left inside the crash window without re-executing them."""
    recovered: list[dict[str, Any]] = []
    db.query(AssistantToolExecution).filter(
        AssistantToolExecution.source == "mcp", AssistantToolExecution.status == "approved",
    ).update({"status": "ambiguous", "result_json": ToolResult.error("Interrupted before execution; submit a fresh read request").as_dict()})
    executions = (
        db.query(AssistantToolExecution)
        .filter(AssistantToolExecution.status == "executing")
        .all()
    )
    for execution in executions:
        run = db.get(Run, execution.external_run_id) if execution.external_run_id else None
        if run is not None:
            result = ToolResult(
                content=json.dumps(
                    {
                        "tool_id": run.run_type,
                        "run_id": run.id,
                        "status": run.status.value,
                        "recovered_after_restart": True,
                    },
                    indent=2,
                ),
                details={"ledger_recovered": True, "run_id": run.id},
            )
            execution.status = "succeeded"
            execution.result_json = result.as_dict()
            execution.error_message = None
        else:
            result = ToolResult(
                content=(
                    "Error: NeuroCade restarted while this tool was executing; "
                    "whether its side effect completed is unknown. It was not retried."
                ),
                is_error=True,
                details={"ledger_status": "ambiguous"},
                terminal=True,
            )
            execution.status = "ambiguous"
            execution.result_json = result.as_dict()
            execution.error_message = result.content
        recovered.append(
            {
                "turn_id": execution.turn_id,
                "call_id": execution.call_id,
                "tool_name": execution.tool_name,
                "status": execution.status,
                "external_run_id": execution.external_run_id,
            }
        )
    db.commit()
    return recovered
