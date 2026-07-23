"""Assistant tool definition types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any]], Awaitable[str]]

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
