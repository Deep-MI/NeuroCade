"""Assistant model and tool orchestration loop.

This module owns the assistant turn state machine. It builds the available
tools, calls the configured chat model with native tool definitions, executes
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
from sqlalchemy.orm import Session

from api_service.assistant.context_budget import ContextBudgeter, message_text, message_tokens
from api_service.assistant.model_protocols import NativeToolProtocol, response_text
from api_service.assistant.prompts import build_model_messages, build_system_prompt
from api_service.assistant.tool_execution_store import AssistantToolExecutionStore
from api_service.assistant.tool_executor import AssistantToolExecutor
from api_service.assistant.tools import AssistantToolBuilder
from backend_common.providers import provider_registry
from backend_common.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
AssistantState = dict[str, Any]
class AssistantLoop:
    """Coordinate model turns and tool calls for one assistant request."""

    def __init__(self, tools: AssistantToolBuilder, *, config_dir: Path) -> None:
        """Store tool builders and prompt configuration roots for the loop."""
        self.tools = tools
        self.config_dir = config_dir
        self.executions = AssistantToolExecutionStore()
        self.tool_executor = AssistantToolExecutor(self.executions)
        self.context_budget = ContextBudgeter(settings)

    async def run(self, state: AssistantState) -> AssistantState:
        """Run the assistant until it returns a final response or fails.

        The loop alternates between model turns and pending tool-call execution.
        State is copied on entry and updated with bootstrap data so callers can
        treat the returned dictionary as the complete final turn state.
        """
        current_state: AssistantState = dict(state)
        current_state.update(await self._bootstrap(current_state))
        self._finish_db_transaction(current_state)

        while True:
            if self._done(current_state):
                return current_state
            if current_state.get("pending_tool_calls"):
                await self._checkpoint(current_state, "tool_batch_pending")
                current_state.update(await self._execute_tools(current_state))
                self._finish_db_transaction(current_state)
                await self._checkpoint(current_state, str(current_state.get("status") or "tool_batch_completed"))
                continue
            self._finish_db_transaction(current_state)
            await self._checkpoint(current_state, "model_running")
            current_state.update(await self._model_turn(current_state))
            await self._checkpoint(
                current_state,
                "tool_planned" if current_state.get("pending_tool_calls") else str(current_state.get("status") or "model_completed"),
            )
            if self._done(current_state) or not current_state.get("pending_tool_calls"):
                return current_state

    @staticmethod
    async def _checkpoint(state: AssistantState, phase: str) -> None:
        sink = state.get("checkpoint_sink")
        if sink is not None:
            await sink(phase, state)

    @staticmethod
    def _finish_db_transaction(state: AssistantState) -> None:
        """Commit short DB work before the loop awaits external model/tool work."""
        db = state.get("db")
        if isinstance(db, Session) and db.in_transaction():
            db.commit()

    async def _bootstrap(self, state: AssistantState) -> dict[str, Any]:
        """Snapshot tools, session context, and the system prompt for this turn."""
        tool_definitions, tool_specs = self.tools.build(state)
        bootstrap = {
            "tool_definitions": tool_definitions,
            "tool_specs": tool_specs,
            "gui_state": self.tools.load_gui_state(state),
            "workspace_cases": self.tools.case_summaries(state),
            "status": "running",
        }
        bootstrap["system_prompt"] = build_system_prompt(
            self.config_dir,
            {**state, **bootstrap},
        )
        return bootstrap

    def _done(self, state: AssistantState) -> bool:
        """Return whether the loop has reached a terminal state."""
        return bool(state.get("error") or state.get("final_response") is not None)

    async def _model_turn(self, state: AssistantState) -> dict[str, Any]:
        """Call a model that supports native provider tool calls."""
        if state.get("event_sink") is not None:
            await state["event_sink"](
                "activity",
                {"kind": "model", "label": "Assistant", "blocking": True},
            )
        provider_config = state["provider_config"]
        request_id = state.get("diagnostic_request_id")
        round_number = state["round_count"] + 1
        model = provider_registry.build_chat_model(
            provider_override=provider_config.provider,
            model_override=provider_config.model,
        )
        if not hasattr(model, "bind_tools"):
            raise HTTPException(status_code=502, detail="Configured model does not support native tool calling")
        model_messages = self._bounded_messages(
            build_model_messages(
                state["system_prompt"],
                state.get("conversation", []),
            )
        )
        invocation_model = model.bind_tools(state["tool_specs"])
        prompt_chars = sum(len(message_text(message)) for message in model_messages)
        prompt_tokens = sum(message_tokens(message) for message in model_messages)
        started_at = time.monotonic()
        logger.info(
            (
                "assistant.model_call.started request_id=%s round=%s provider=%s model=%s "
                "message_count=%s prompt_chars=%s prompt_tokens=%s"
            ),
            request_id,
            round_number,
            provider_config.provider,
            provider_config.model,
            len(model_messages),
            prompt_chars,
            prompt_tokens,
        )
        try:
            response = await self._invoke_model(invocation_model, model_messages, state, round_number)
        except asyncio.CancelledError:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.warning("assistant.model_call.cancelled request_id=%s round=%s elapsed_ms=%s", request_id, round_number, elapsed_ms)
            raise
        except Exception:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.exception("assistant.model_call.failed request_id=%s round=%s elapsed_ms=%s", request_id, round_number, elapsed_ms)
            raise

        raw_text = response_text(response)
        model_elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info("assistant.model_call.completed request_id=%s round=%s elapsed_ms=%s raw_chars=%s", request_id, round_number, model_elapsed_ms, len(raw_text))

        protocol_result = NativeToolProtocol.parse(response)
        planned_calls = protocol_result.calls
        assistant_message = protocol_result.assistant_message
        reasoning_entry = await self._emit_native_progress(
            state,
            assistant_message=assistant_message,
            reasoning=protocol_result.reasoning,
            planned_calls=planned_calls,
            round_number=round_number,
        )

        if not planned_calls:
            if not protocol_result.final_content:
                raise HTTPException(status_code=502, detail="Assistant model returned neither content nor a tool call")
            return {
                "round_count": round_number,
                "reasoning_entries": state.get("reasoning_entries", []) + ([reasoning_entry] if reasoning_entry else []),
                "final_response": protocol_result.final_content,
                "usage": protocol_result.usage,
                "status": "completed",
            }

        conversation = list(state.get("conversation", []))
        conversation.append(
            {
                "role": "assistant",
                "content": raw_text,
                "tool_calls": [
                    {"id": call["call_id"], "name": call["name"], "args": call["arguments"], "type": "tool_call"}
                    for call in planned_calls
                ],
            }
        )
        assistant_messages = list(state.get("assistant_messages", []))
        if assistant_message:
            assistant_messages.append(assistant_message)
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
            "usage": protocol_result.usage,
            "status": "running",
        }

    async def _invoke_model(
        self,
        model: Any,
        messages: list[BaseMessage],
        state: AssistantState,
        round_number: int,
    ) -> Any:
        """Stream when supported while accumulating one standard AI message."""
        if state.get("event_sink") is None or not hasattr(model, "astream"):
            return await model.ainvoke(messages)
        aggregate = None
        emitted_delta = False
        try:
            async for chunk in model.astream(messages):
                aggregate = chunk if aggregate is None else aggregate + chunk
                text = response_text(chunk)
                if text:
                    emitted_delta = True
                    state.setdefault("streamed_text_rounds", set()).add(round_number)
                    await state["event_sink"]("text_delta", {"content": str(text), "round": round_number})
                for tool_chunk in getattr(chunk, "tool_call_chunks", []) or []:
                    emitted_delta = True
                    await state["event_sink"]("tool_call_delta", {"round": round_number, **dict(tool_chunk)})
        except Exception as exc:
            if emitted_delta:
                raise
            logger.warning(
                "assistant.model_stream.failed_falling_back round=%s error_type=%s",
                round_number,
                type(exc).__name__,
            )
            return await model.ainvoke(messages)
        if aggregate is None:
            raise HTTPException(status_code=502, detail="Assistant model stream ended without a response")
        return aggregate

    async def _emit_native_progress(
        self,
        state: AssistantState,
        *,
        assistant_message: str | None,
        reasoning: str | None,
        planned_calls: list[dict[str, Any]],
        round_number: int,
    ) -> dict[str, Any] | None:
        if (
            assistant_message
            and planned_calls
            and state.get("event_sink") is not None
            and round_number not in state.get("streamed_text_rounds", set())
        ):
            await state["event_sink"]("assistant_message", {"content": assistant_message, "round": round_number})
        if not reasoning:
            return None
        entry = {
            "summary": reasoning[:2000],
            "round": round_number,
            "tool_names": [call["name"] for call in planned_calls],
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
        return await self.tool_executor.execute(state)


    def _bounded_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        return self.context_budget.bound(messages)
