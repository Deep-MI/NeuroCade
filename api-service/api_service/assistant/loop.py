"""Assistant model and tool orchestration loop.

This module owns the assistant turn state machine. It builds the available
tools, calls the configured chat model for structured JSON responses, executes
requested tools, appends tool results back into the conversation, emits optional
streaming events, and stops when a final response or terminal error is reached.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import BaseMessage

from api_service.assistant.structured_response import AssistantStructuredResponse, AssistantToolCall, coerce_structured_response
from api_service.assistant.prompts import build_structured_response_messages, build_system_prompt, stringify_content
from api_service.assistant.tools.definition import ToolDefinition
from api_service.assistant.tools import AssistantToolBuilder
from backend_common.providers import ProviderRole, provider_registry


logger = logging.getLogger(__name__)
AssistantState = dict[str, Any]


def _message_text(message: BaseMessage) -> str:
    """Return normalized text content for prompt-size diagnostics."""
    return stringify_content(getattr(message, "content", ""))


class AssistantLoop:
    """Coordinate model turns and tool calls for one assistant request."""

    def __init__(self, tools: AssistantToolBuilder, *, config_dir: Path) -> None:
        """Store tool builders and prompt configuration roots for the loop."""
        self.tools = tools
        self.config_dir = config_dir

    async def run(self, state: AssistantState) -> AssistantState:
        """Run the assistant until it returns a final response or fails.

        The loop alternates between model turns and pending tool-call execution.
        State is copied on entry and updated with bootstrap data so callers can
        treat the returned dictionary as the complete final turn state.
        """
        current_state: AssistantState = dict(state)
        current_state.update(await self._bootstrap(current_state))

        while True:
            if self._done(current_state):
                return current_state
            if current_state.get("pending_tool_calls"):
                current_state.update(await self._execute_tools(current_state))
                continue
            current_state.update(await self._model_turn(current_state))
            if self._done(current_state) or not current_state.get("pending_tool_calls"):
                return current_state

    async def _bootstrap(self, state: AssistantState) -> dict[str, Any]:
        """Load tools, GUI context, and workspace summaries before round one."""
        tool_definitions, tool_specs = await self.tools.build(state)
        return {
            "tool_definitions": tool_definitions,
            "tool_specs": tool_specs,
            "gui_state": await self.tools.load_gui_state(state),
            "workspace_cases": self.tools.case_summaries(state),
            "status": "running",
        }

    def _done(self, state: AssistantState) -> bool:
        """Return whether the loop has reached a terminal state."""
        return bool(state.get("error") or state.get("final_response") is not None)

    async def _model_turn(self, state: AssistantState) -> dict[str, Any]:
        """Call the model once and convert its structured response into state.

        A final response completes the turn. A tool-call response records any
        assistant-facing message and reasoning, normalizes planned tool calls,
        and leaves those calls in ``pending_tool_calls`` for the next loop pass.
        """
        provider_config = state["provider_config"]
        request_id = state.get("diagnostic_request_id")
        round_number = state["round_count"] + 1
        model = provider_registry.build_chat_model(
            ProviderRole.chat,
            provider_override=provider_config.provider,
            model_override=provider_config.model,
        )
        model_messages = build_structured_response_messages(build_system_prompt(self.config_dir, state), state.get("conversation", []))
        prompt_chars = sum(len(_message_text(message)) for message in model_messages)
        started_at = time.monotonic()
        logger.info(
            (
                "assistant.model_call.started request_id=%s round=%s provider=%s model=%s "
                "message_count=%s prompt_chars=%s"
            ),
            request_id,
            round_number,
            provider_config.provider,
            provider_config.model,
            len(model_messages),
            prompt_chars,
        )
        try:
            response = await model.ainvoke(model_messages)
        except asyncio.CancelledError:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.warning("assistant.model_call.cancelled request_id=%s round=%s elapsed_ms=%s", request_id, round_number, elapsed_ms)
            raise
        except Exception:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.exception("assistant.model_call.failed request_id=%s round=%s elapsed_ms=%s", request_id, round_number, elapsed_ms)
            raise

        raw_text = self._response_text(response)
        model_elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info("assistant.model_call.completed request_id=%s round=%s elapsed_ms=%s raw_chars=%s", request_id, round_number, model_elapsed_ms, len(raw_text))

        parse_started_at = time.monotonic()
        parsed = await self._coerce_structured_response(model, model_messages, raw_text)
        parsed_tool_calls = self._normalize_tool_calls(parsed.tool_calls)
        parse_elapsed_ms = int((time.monotonic() - parse_started_at) * 1000)
        logger.info(
            "assistant.structured_response_parse.completed request_id=%s round=%s elapsed_ms=%s kind=%s tool_count=%s",
            request_id,
            round_number,
            parse_elapsed_ms,
            parsed.kind,
            len(parsed_tool_calls),
        )

        assistant_message = self._assistant_message(parsed, state, round_number)
        reasoning_entry = await self._reasoning_entry(parsed, parsed_tool_calls, state, round_number)

        if parsed.kind == "final":
            return {
                "round_count": round_number,
                "reasoning_entries": state.get("reasoning_entries", []) + ([reasoning_entry] if reasoning_entry else []),
                "final_response": parsed.content or "",
                "status": "completed",
            }

        planned_calls = [call.model_dump() for call in parsed_tool_calls]
        conversation = list(state.get("conversation", []))
        assistant_messages = list(state.get("assistant_messages", []))
        if assistant_message:
            assistant_messages.append(assistant_message)
            conversation.append({"role": "assistant", "content": assistant_message})
        if round_number > state["max_rounds"]:
            return {
                "error": f"I used all {state['max_rounds']} steps without finishing the task.",
                "status": "failed",
            }

        return {
            "conversation": conversation,
            "round_count": round_number,
            "reasoning_entries": state.get("reasoning_entries", []) + ([reasoning_entry] if reasoning_entry else []),
            "assistant_messages": assistant_messages,
            "pending_tool_calls": planned_calls,
            "status": "running",
        }

    def _response_text(self, response: Any) -> str:
        """Extract text from a chat-model response object."""
        raw_content = getattr(response, "content", "")
        if isinstance(raw_content, list):
            return "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in raw_content)
        return str(raw_content or "")

    def _normalize_tool_calls(self, tool_calls: list[Any]) -> list[AssistantToolCall]:
        """Keep only validated assistant tool-call objects."""
        return [call for call in tool_calls or [] if isinstance(call, AssistantToolCall)]

    async def _coerce_structured_response(
        self,
        model: Any,
        messages: list[BaseMessage],
        raw_text: str,
    ) -> AssistantStructuredResponse:
        """Parse or repair the model response into the assistant JSON schema."""
        return await coerce_structured_response(model, messages, raw_text)

    def _assistant_message(self, parsed: AssistantStructuredResponse, state: AssistantState, round_number: int) -> str | None:
        """Return the assistant-facing interim message for a tool-call turn."""
        if not parsed.message or parsed.kind != "tool_calls":
            return None
        assistant_message = parsed.message.strip()[:4000]
        if assistant_message and state.get("event_sink") is not None:
            return assistant_message
        return assistant_message or None

    async def _reasoning_entry(
        self,
        parsed: AssistantStructuredResponse,
        parsed_tool_calls: list[AssistantToolCall],
        state: AssistantState,
        round_number: int,
    ) -> dict[str, Any] | None:
        """Create and optionally stream a compact reasoning record for a turn."""
        if parsed.message and parsed.kind == "tool_calls":
            assistant_message = parsed.message.strip()[:4000]
            if assistant_message and state.get("event_sink") is not None:
                await state["event_sink"]("assistant_message", {"content": assistant_message, "round": round_number})
        if not parsed.reasoning:
            return None
        entry = {
            "summary": parsed.reasoning[:2000],
            "round": round_number,
            "tool_names": [call.name for call in parsed_tool_calls],
        }
        if state.get("event_sink") is not None:
            await state["event_sink"]("reasoning", entry)
        return entry

    async def _execute_tools(self, state: AssistantState) -> dict[str, Any]:
        """Execute all pending tool calls and append their results to state.

        Tool names are matched case-insensitively. Exceptions are converted into
        assistant-visible ``Error:`` tool results, except cancellation, which is
        re-raised so request shutdown can propagate normally.
        """
        definitions = list(state.get("tool_definitions", []))
        tool_map: dict[str, ToolDefinition] = {}
        for definition in definitions:
            tool_map[definition.name] = definition
            tool_map[definition.name.lower()] = definition

        conversation = list(state.get("conversation", []))
        tool_logs = list(state.get("tool_calls_log", []))
        result: dict[str, Any] = dict(state.get("result", {}))
        request_id = state.get("diagnostic_request_id")
        for planned in state.get("pending_tool_calls", []):
            planned_name = str(planned.get("name") or "")
            tool = tool_map.get(planned_name) or tool_map.get(planned_name.lower())
            if tool is None:
                return await self._unknown_tool(state, definitions, planned_name, planned, result)
            arguments = planned.get("arguments", {})
            started_at = time.monotonic()
            logger.info("assistant.tool_call.started request_id=%s round=%s tool=%s", request_id, state.get("round_count"), tool.name)
            try:
                tool_result = await tool.execute(arguments)
            except asyncio.CancelledError:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.warning("assistant.tool_call.cancelled request_id=%s round=%s tool=%s elapsed_ms=%s", request_id, state.get("round_count"), tool.name, elapsed_ms)
                raise
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                tool_result = self._tool_exception_result(exc)
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

            entry = await self._record_tool_result(state, tool, arguments, tool_result, started_at)
            tool_logs.append(entry)
            if state.get("event_sink") is not None:
                await state["event_sink"]("tool_call", entry)
            conversation.append({"role": "tool", "content": f"[Tool result] {tool.name}: {tool_result}"})
            if tool.name == "gui_run_fastsurfer":
                result["case_id"] = state.get("case_id")
        return {
            "conversation": conversation,
            "pending_tool_calls": [],
            "tool_calls_log": tool_logs,
            "result": result,
            "status": "running",
        }

    async def _unknown_tool(
        self,
        state: AssistantState,
        definitions: list[ToolDefinition],
        planned_name: str,
        planned: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Record an assistant-visible error for a requested unknown tool."""
        unknown_details = {
            "round": state.get("round_count"),
            "tool": planned_name,
            "arguments": planned.get("arguments", {}),
            "available_tools": sorted({definition.name for definition in definitions}),
        }
        logger.warning(
            "assistant.tool_call.unknown request_id=%s round=%s tool=%s available_tools=%s",
            state.get("diagnostic_request_id"),
            state.get("round_count"),
            planned_name,
            ",".join(unknown_details["available_tools"]),
        )
        result["unknown_tool_call"] = unknown_details
        tool_result = (
            f"Error: Unknown tool `{planned_name}`. "
            "Available tools: " + ", ".join(unknown_details["available_tools"])
        )
        entry = {
            "name": planned_name,
            "arguments": planned.get("arguments", {}),
            "result": tool_result[:2000],
            "elapsed_ms": 0,
        }
        if state.get("event_sink") is not None:
            await state["event_sink"]("tool_call", entry)
        return {
            "conversation": list(state.get("conversation", [])) + [{"role": "tool", "content": f"[Tool result] {planned_name}: {tool_result}"}],
            "pending_tool_calls": [],
            "tool_calls_log": list(state.get("tool_calls_log", [])) + [entry],
            "result": result,
            "status": "running",
        }

    async def _record_tool_result(
        self,
        state: AssistantState,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        tool_result: str,
        started_at: float,
    ) -> dict[str, Any]:
        """Log and summarize a completed tool call for persistence and events."""
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        is_error = tool_result.lstrip().startswith("Error:")
        log_message = (
            "assistant.tool_call.failed request_id=%s round=%s tool=%s elapsed_ms=%s result_chars=%s"
            if is_error
            else "assistant.tool_call.completed request_id=%s round=%s tool=%s elapsed_ms=%s result_chars=%s"
        )
        log_fn = logger.warning if is_error else logger.info
        log_fn(log_message, state.get("diagnostic_request_id"), state.get("round_count"), tool.name, elapsed_ms, len(tool_result))
        return {
            "name": tool.name,
            "arguments": arguments,
            "result": tool_result[:2000],
            "elapsed_ms": elapsed_ms,
        }

    def _tool_exception_result(self, exc: Exception) -> str:
        """Render a tool exception as a short assistant-visible error string."""
        if isinstance(exc, HTTPException):
            detail = exc.detail if isinstance(exc.detail, str) else "Tool request failed"
            return f"Error: {detail}"
        return f"Error: {exc}"
