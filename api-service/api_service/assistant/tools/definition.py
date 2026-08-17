"""Assistant tool definition types."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolRisk(str, Enum):
    read = "read"
    gui = "gui"
    write = "write"
    workflow = "workflow"

    @property
    def requires_confirmation(self) -> bool:
        return self in {ToolRisk.write, ToolRisk.workflow}


@dataclass(frozen=True)
class ToolResult:
    """Provider-independent result returned by an assistant tool."""

    content: str
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False

    @classmethod
    def success(
        cls,
        content: str,
        *,
        details: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        terminal: bool = False,
    ) -> ToolResult:
        return cls(
            content=content,
            details=dict(details or {}),
            artifacts=list(artifacts or []),
            terminal=terminal,
        )

    @classmethod
    def error(
        cls,
        content: str,
        *,
        details: dict[str, Any] | None = None,
        terminal: bool = False,
    ) -> ToolResult:
        return cls(
            content=content,
            is_error=True,
            details=dict(details or {}),
            terminal=terminal,
        )

    @classmethod
    def structured(
        cls,
        payload: Any,
        *,
        details: dict[str, Any] | None = None,
        is_error: bool = False,
    ) -> ToolResult:
        """Render a JSON tool result while retaining its structured details."""
        resolved_details = payload if details is None and isinstance(payload, dict) else details
        factory = cls.error if is_error else cls.success
        return factory(json.dumps(payload, indent=2), details=resolved_details)

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "is_error": self.is_error,
            "details": self.details,
            "artifacts": self.artifacts,
            "terminal": self.terminal,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ToolResult:
        return cls(
            content=str(value.get("content") or ""),
            is_error=bool(value.get("is_error", False)),
            details=dict(value.get("details") or {}),
            artifacts=list(value.get("artifacts") or []),
            terminal=bool(value.get("terminal", False)),
        )


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[ToolExecutionContext, dict[str, Any]], Awaitable[ToolResult]]
    risk: ToolRisk = ToolRisk.read
    parallel_safe: bool = False

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolExecutionContext:
    """Stable execution identity supplied explicitly to every tool handler."""

    call_id: str
    turn_id: str | None = None
    execution_id: str | None = None
    external_run_id: str | None = None
