"""Assistant tool execution mechanics, independent of model protocols."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.approval_presentations import approval_description, build_approval_presentation
from api_service.assistant.tool_execution_store import AssistantToolExecutionStore, approval_digest
from api_service.assistant.tool_results import ToolResultRenderer
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from backend_common.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
GUI_ACK_POLL_INTERVAL_SECONDS = 0.25
AssistantState = dict[str, Any]


class AssistantToolExecutor:
    """Execute, replay, render, and record already-planned tool calls."""

    def __init__(self, executions: AssistantToolExecutionStore) -> None:
        self.executions = executions

    @staticmethod
    async def execute_tool(
        tool: ToolDefinition,
        execution_context: ToolExecutionContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        if tool.name != "gui_command_status":
            return await tool.execute(execution_context, arguments)
        deadline = time.monotonic() + settings.assistant_gui_ack_wait_seconds
        while True:
            result = await tool.execute(execution_context, arguments)
            try:
                if json.loads(result.content).get("status") != "pending":
                    return result
            except (json.JSONDecodeError, AttributeError, TypeError):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            await asyncio.sleep(min(GUI_ACK_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def fingerprint(name: str, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"name": name.lower(), "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def approval_request(
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
        execution_id: str | None = None,
        presentation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = {
            "name": name,
            "call_id": call_id,
            "execution_id": execution_id,
            "arguments": arguments,
            "digest": approval_digest(name, arguments),
            "description": approval_description(name, arguments, presentation),
        }
        if presentation is not None:
            request["presentation"] = presentation
        return request

    @classmethod
    def consume_approval(
        cls,
        state: AssistantState,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> bool:
        requested = cls.approval_request(name, arguments, call_id=call_id)
        approvals = state.setdefault("tool_approvals", [])
        for index, approval in enumerate(approvals):
            if (
                approval.get("name") == requested["name"]
                and approval.get("arguments") == requested["arguments"]
                and approval.get("digest") == requested["digest"]
                and (not call_id or approval.get("call_id") == call_id)
            ):
                approvals.pop(index)
                return True
        return False

    async def execute(self, state: AssistantState) -> dict[str, Any]:
        """Execute the pending batch, enforcing replay, approval, and safety policy."""
        definitions = list(state.get("tool_definitions", []))
        tool_map: dict[str, ToolDefinition] = {}
        for definition in definitions:
            tool_map[definition.name] = definition
            tool_map[definition.name.lower()] = definition

        conversation = list(state.get("conversation", []))
        tool_logs = list(state.get("tool_calls_log", []))
        result: dict[str, Any] = dict(state.get("result", {}))
        request_id = state.get("diagnostic_request_id")
        pending_calls = list(state.get("pending_tool_calls", []))
        resolved_batch = [
            tool_map.get(str(call.get("name") or ""))
            or tool_map.get(str(call.get("name") or "").lower())
            for call in pending_calls
        ]
        fingerprints = [
            self.fingerprint(str(call.get("name") or ""), call.get("arguments", {}))
            for call in pending_calls
        ]
        if (
            len(pending_calls) > 1
            and all(
                tool is not None and tool.risk.value == "read" and tool.parallel_safe
                for tool in resolved_batch
            )
            and len(set(fingerprints)) == len(fingerprints)
            and state.get("last_tool_call_fingerprint") not in fingerprints
        ):
            return await self.execute_parallel(
                state,
                [tool for tool in resolved_batch if tool is not None],
                pending_calls,
                conversation,
                tool_logs,
                result,
            )

        fingerprint = state.get("last_tool_call_fingerprint")
        for pending_index, planned in enumerate(pending_calls):
            call_id = str(planned.get("call_id") or uuid4())
            planned["call_id"] = call_id
            planned_name = str(planned.get("name") or "")
            tool = tool_map.get(planned_name) or tool_map.get(planned_name.lower())
            if tool is None:
                return await self.unknown_tool(
                    state,
                    definitions,
                    planned_name,
                    {**planned, "call_id": call_id},
                    result,
                )
            arguments = planned.get("arguments", {})
            fingerprint = self.fingerprint(tool.name, arguments)
            if fingerprint == state.get("last_tool_call_fingerprint"):
                return {
                    "conversation": conversation,
                    "pending_tool_calls": pending_calls[pending_index:],
                    "tool_calls_log": tool_logs,
                    "result": result,
                    "final_response": (
                        f"I stopped because `{tool.name}` was requested again with identical arguments "
                        "and no new evidence or state change. Try a narrower read, a text search, or different arguments."
                    ),
                    "status": "completed",
                }
            db = state.get("db") if isinstance(state.get("db"), Session) else None
            execution = self.executions.plan(
                db,
                turn_id=state.get("turn_id"),
                tool=tool,
                call_id=call_id,
                arguments=arguments,
                approved=not tool.risk.requires_confirmation,
            )
            if tool.risk.requires_confirmation and (execution is None or execution.status == "planned"):
                if not self.consume_approval(state, tool.name, arguments, call_id=call_id):
                    presentation = build_approval_presentation(state, tool.name, arguments)
                    approval_request = self.approval_request(
                        tool.name,
                        arguments,
                        call_id=call_id,
                        execution_id=getattr(execution, "id", None),
                        presentation=presentation,
                    )
                    if state.get("event_sink") is not None:
                        await state["event_sink"]("approval_required", approval_request)
                    return {
                        "conversation": conversation,
                        "pending_tool_calls": pending_calls[pending_index:],
                        "tool_calls_log": tool_logs,
                        "result": result,
                        "approval_request": approval_request,
                        "final_response": f"Please confirm that I may {approval_request['description']}.",
                        "status": "awaiting_approval",
                    }
                self.executions.approve(db, execution)

            replayed = self.executions.begin(db, execution)
            started_at = time.monotonic()
            logger.info(
                "assistant.tool_call.started request_id=%s round=%s tool=%s",
                request_id,
                state.get("round_count"),
                tool.name,
            )
            if replayed is not None:
                tool_result = replayed
            else:
                context = ToolExecutionContext(
                    call_id=call_id,
                    turn_id=state.get("turn_id"),
                    execution_id=getattr(execution, "id", None),
                    external_run_id=getattr(execution, "external_run_id", None),
                )
                try:
                    tool_result = await self.execute_tool(tool, context, arguments)
                except asyncio.CancelledError:
                    elapsed_ms = int((time.monotonic() - started_at) * 1000)
                    logger.warning(
                        "assistant.tool_call.cancelled request_id=%s round=%s tool=%s elapsed_ms=%s",
                        request_id,
                        state.get("round_count"),
                        tool.name,
                        elapsed_ms,
                    )
                    self.executions.interrupt(
                        db,
                        execution,
                        reason="The assistant request was canceled while this tool was executing.",
                    )
                    raise
                except Exception as exc:
                    if db is not None and db.in_transaction():
                        db.rollback()
                    elapsed_ms = int((time.monotonic() - started_at) * 1000)
                    tool_result = ToolResultRenderer.from_exception(exc)
                    if isinstance(exc, HTTPException):
                        logger.warning(
                            "assistant.tool_call.failed request_id=%s round=%s tool=%s elapsed_ms=%s status_code=%s detail=%s",
                            request_id,
                            state.get("round_count"),
                            tool.name,
                            elapsed_ms,
                            exc.status_code,
                            exc.detail,
                        )
                    else:
                        logger.exception(
                            "assistant.tool_call.failed request_id=%s round=%s tool=%s elapsed_ms=%s",
                            request_id,
                            state.get("round_count"),
                            tool.name,
                            elapsed_ms,
                        )
                self.executions.complete(db, execution, tool_result)

            entry = await self.record_result(
                state,
                tool,
                call_id,
                arguments,
                tool_result,
                started_at,
                execution=execution,
            )
            tool_logs.append(entry)
            if state.get("event_sink") is not None:
                await state["event_sink"]("tool_call", entry)
            conversation.append({
                "role": "tool",
                "name": tool.name,
                "call_id": call_id,
                "content": ToolResultRenderer.for_model(tool.name, tool_result),
            })
            if tool_result.terminal:
                return {
                    "conversation": conversation,
                    "pending_tool_calls": [],
                    "tool_calls_log": tool_logs,
                    "result": result,
                    "last_tool_call_fingerprint": fingerprint,
                    "final_response": tool_result.content,
                    "status": "completed",
                }
            queued = ToolResultRenderer.queued_workflow(tool.name, tool_result)
            if queued is not None:
                tool_id, run_id = queued
                return {
                    "conversation": conversation,
                    "pending_tool_calls": [],
                    "tool_calls_log": tool_logs,
                    "result": result,
                    "last_tool_call_fingerprint": fingerprint,
                    "final_response": (
                        f"Workflow `{tool_id}` was queued successfully (run `{run_id}`). "
                        "Progress and completion will appear in the analysis output."
                    ),
                    "status": "completed",
                }
        return {
            "conversation": conversation,
            "pending_tool_calls": [],
            "tool_calls_log": tool_logs,
            "result": result,
            "last_tool_call_fingerprint": fingerprint,
            "status": "running",
        }

    async def execute_parallel(
        self,
        state: AssistantState,
        tools: list[ToolDefinition],
        pending_calls: list[dict[str, Any]],
        conversation: list[dict[str, Any]],
        tool_logs: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an explicitly safe read batch concurrently in planned order."""
        request_id = state.get("diagnostic_request_id")
        db = state.get("db") if isinstance(state.get("db"), Session) else None
        prepared = []
        for tool, planned in zip(tools, pending_calls, strict=True):
            call_id = str(planned.get("call_id") or uuid4())
            arguments = dict(planned.get("arguments", {}))
            execution = self.executions.plan(
                db,
                turn_id=state.get("turn_id"),
                tool=tool,
                call_id=call_id,
                arguments=arguments,
                approved=True,
            )
            replayed = self.executions.begin(db, execution)
            prepared.append((tool, call_id, arguments, execution, replayed))

        async def execute_one(tool, call_id, arguments, execution, replayed):
            started_at = time.monotonic()
            logger.info(
                "assistant.tool_call.started request_id=%s round=%s tool=%s parallel=true",
                request_id,
                state.get("round_count"),
                tool.name,
            )
            if replayed is not None:
                return tool, call_id, arguments, execution, replayed, started_at, False
            try:
                context = ToolExecutionContext(
                    call_id=call_id,
                    turn_id=state.get("turn_id"),
                    execution_id=getattr(execution, "id", None),
                    external_run_id=getattr(execution, "external_run_id", None),
                )
                tool_result = await self.execute_tool(tool, context, arguments)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                tool_result = ToolResultRenderer.from_exception(exc)
            return tool, call_id, arguments, execution, tool_result, started_at, True

        try:
            completed = await asyncio.gather(*(execute_one(*item) for item in prepared))
        except asyncio.CancelledError:
            for _tool, _call_id, _arguments, execution, replayed in prepared:
                if replayed is None:
                    self.executions.interrupt(
                        db,
                        execution,
                        reason="The assistant request was canceled during a parallel tool batch.",
                    )
            raise

        terminal_result = None
        for tool, call_id, arguments, execution, tool_result, started_at, executed in completed:
            if executed:
                self.executions.complete(db, execution, tool_result)
            entry = await self.record_result(
                state,
                tool,
                call_id,
                arguments,
                tool_result,
                started_at,
                execution=execution,
            )
            tool_logs.append(entry)
            if state.get("event_sink") is not None:
                await state["event_sink"]("tool_call", entry)
            conversation.append({
                "role": "tool",
                "name": tool.name,
                "call_id": call_id,
                "content": ToolResultRenderer.for_model(tool.name, tool_result),
            })
            terminal_result = terminal_result or (tool_result if tool_result.terminal else None)
        if terminal_result is not None:
            return {
                "conversation": conversation,
                "pending_tool_calls": [],
                "tool_calls_log": tool_logs,
                "result": result,
                "final_response": terminal_result.content,
                "status": "completed",
            }
        return {
            "conversation": conversation,
            "pending_tool_calls": [],
            "tool_calls_log": tool_logs,
            "result": result,
            "last_tool_call_fingerprint": self.fingerprint(
                tools[-1].name,
                pending_calls[-1].get("arguments", {}),
            ),
            "status": "running",
        }

    async def unknown_tool(
        self,
        state: AssistantState,
        definitions: list[ToolDefinition],
        planned_name: str,
        planned: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        available = sorted({definition.name for definition in definitions})
        logger.warning(
            "assistant.tool_call.unknown request_id=%s round=%s tool=%s available_tools=%s",
            state.get("diagnostic_request_id"),
            state.get("round_count"),
            planned_name,
            ",".join(available),
        )
        result["unknown_tool_call"] = {
            "round": state.get("round_count"),
            "tool": planned_name,
            "arguments": planned.get("arguments", {}),
            "available_tools": available,
        }
        tool_result = ToolResult.error(
            f"Error: Unknown tool `{planned_name}`. Available tools: " + ", ".join(available)
        )
        entry = {
            "call_id": planned.get("call_id"),
            "name": planned_name,
            "arguments": planned.get("arguments", {}),
            "result": ToolResultRenderer.for_display(tool_result),
            "is_error": True,
            "elapsed_ms": 0,
        }
        if state.get("event_sink") is not None:
            await state["event_sink"]("tool_call", entry)
        return {
            "conversation": list(state.get("conversation", [])) + [{
                "role": "tool",
                "name": planned_name,
                "call_id": planned.get("call_id"),
                "content": f"[Tool result] {planned_name}: {tool_result.content}",
            }],
            "pending_tool_calls": [],
            "tool_calls_log": list(state.get("tool_calls_log", [])) + [entry],
            "result": result,
            "status": "running",
        }

    @staticmethod
    async def record_result(
        state: AssistantState,
        tool: ToolDefinition,
        call_id: str,
        arguments: dict[str, Any],
        tool_result: ToolResult,
        started_at: float,
        *,
        execution: Any | None = None,
    ) -> dict[str, Any]:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_message = (
            "assistant.tool_call.failed request_id=%s round=%s tool=%s elapsed_ms=%s result_chars=%s"
            if tool_result.is_error
            else "assistant.tool_call.completed request_id=%s round=%s tool=%s elapsed_ms=%s result_chars=%s"
        )
        (logger.warning if tool_result.is_error else logger.info)(
            log_message,
            state.get("diagnostic_request_id"),
            state.get("round_count"),
            tool.name,
            elapsed_ms,
            len(tool_result.content),
        )
        return {
            "call_id": call_id,
            "name": tool.name,
            "arguments": arguments,
            "result": ToolResultRenderer.for_display(tool_result),
            "is_error": tool_result.is_error,
            "details": tool_result.details,
            "artifacts": tool_result.artifacts,
            "terminal": tool_result.terminal,
            "execution_id": getattr(execution, "id", None),
            "ledger_status": getattr(execution, "status", None),
            "external_run_id": getattr(execution, "external_run_id", None),
            "elapsed_ms": elapsed_ms,
        }
