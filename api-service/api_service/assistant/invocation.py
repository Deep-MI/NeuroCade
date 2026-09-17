"""Shared single-handler execution, independent of any conversation or model."""

import asyncio
import json
import time

from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from backend_common.settings import get_settings


class ToolInvocationService:
    @staticmethod
    async def execute_tool(tool: ToolDefinition, execution_context: ToolExecutionContext, arguments: dict) -> ToolResult:
        deadline = time.monotonic() + get_settings().assistant_gui_ack_wait_seconds
        while True:
            result = await tool.execute(execution_context, arguments)
            if tool.name != "gui_command_status":
                return result
            try:
                if json.loads(result.content).get("status") != "pending":
                    return result
            except (ValueError, AttributeError, TypeError):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            await asyncio.sleep(min(0.25, remaining))

    @staticmethod
    def approval_presentation(tool, arguments):
        if not tool.risk.requires_confirmation:
            return None
        if tool.approval_presentation is None:
            raise ValueError("Confirmation tool has no approval presentation")
        return tool.approval_presentation(arguments)

    @staticmethod
    async def execute_claimed(tool, context, arguments, *, store, db, execution, handler=None):
        """Validate and execute a claimed invocation, recording all terminal outcomes."""
        from jsonschema import validate

        from api_service.assistant.tool_results import ToolResultRenderer

        # Durable callers must supply the identity of a currently claimed row.
        # Reject misuse before the exception-to-result handler can overwrite it.
        if db is not None or execution is not None:
            if db is None or execution is None:
                raise ValueError("Durable execution requires a database and claimed invocation")
            db.refresh(execution)
            if (
                execution.status != "executing"
                or context.execution_id != execution.id
                or context.call_id != execution.call_id
                or execution.tool_name != tool.name
                or execution.arguments_json != arguments
            ):
                raise ValueError("Invocation is not claimed for this tool and arguments")
        try:
            validate(arguments, tool.parameters)
            result = await (handler or ToolInvocationService.execute_tool)(tool, context, arguments)
        except asyncio.CancelledError:
            store.interrupt(db, execution, reason="Tool invocation interrupted")
            raise
        except Exception as exc:
            if db is not None:
                db.rollback()
            result = ToolResultRenderer.from_exception(exc)
        store.complete(db, execution, result)
        return result
