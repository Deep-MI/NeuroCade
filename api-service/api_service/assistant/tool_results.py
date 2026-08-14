"""Rendering helpers for structured assistant tool outcomes.

Tool handlers return provider-independent data.  This module is the only place
that turns those outcomes into model context, UI summaries, or exception text.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from api_service.assistant.context_budget import compact_text
from api_service.assistant.tools.definition import ToolResult

PROMPT_TOOL_RESULT_CHARACTERS = 40_000
DISPLAY_TOOL_RESULT_CHARACTERS = 8_000


class ToolResultRenderer:
    """Project a structured result for each consumer without changing storage."""

    @staticmethod
    def for_model(tool_name: str, result: ToolResult) -> str:
        return compact_text(
            f"{tool_name}: {result.content}",
            PROMPT_TOOL_RESULT_CHARACTERS,
        )

    @staticmethod
    def for_display(result: ToolResult) -> str:
        return compact_text(result.content, DISPLAY_TOOL_RESULT_CHARACTERS)

    @staticmethod
    def from_exception(exc: Exception) -> ToolResult:
        if isinstance(exc, HTTPException):
            detail = exc.detail if isinstance(exc.detail, str) else "Tool request failed"
            return ToolResult.error(f"Error: {detail}")
        return ToolResult.error(f"Error: {exc}")

    @staticmethod
    def queued_workflow(tool_name: str, result: ToolResult) -> tuple[str, str] | None:
        if tool_name != "tool_call" or result.is_error:
            return None
        payload: Any = result.details
        if not isinstance(payload, dict):
            try:
                payload = json.loads(result.content)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(payload, dict) or payload.get("status") != "queued":
            return None
        tool_id = payload.get("tool_id")
        run_id = payload.get("run_id")
        return (tool_id, run_id) if isinstance(tool_id, str) and isinstance(run_id, str) else None
