"""Shared runtime tool data types for API service integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolTextContent:
    type: str
    text: str


def text_response(text: str) -> list[ToolTextContent]:
    """Return one text response item for runtime tool handlers."""
    return [ToolTextContent(type="text", text=text)]


def error_response(message: str) -> list[ToolTextContent]:
    """Return one standardized runtime tool error response."""
    return text_response(message if message.startswith("Error:") else f"Error: {message}")


@dataclass(frozen=True)
class RuntimeToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def as_openai_tool(self) -> dict[str, Any]:
        """Return this runtime tool spec in OpenAI function-tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
