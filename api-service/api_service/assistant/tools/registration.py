"""Shared assistant tool registration metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AssistantToolScope(str, Enum):
    """Assistant scopes that can expose a tool."""

    workspace = "workspace"
    case = "case"


ToolDescription = str | Callable[[dict[str, Any]], str]
ToolParameters = dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ScopedToolRegistration:
    """Declarative metadata for one assistant-owned tool."""

    name: str
    description: ToolDescription
    parameters: ToolParameters
    handler_name: str
    scopes: frozenset[AssistantToolScope]
    requires_managed_bash: bool = False

    def exposed_in(self, scope: str) -> bool:
        """Return whether this tool should be exposed in the requested scope."""
        try:
            normalized_scope = AssistantToolScope(scope)
        except ValueError:
            return False
        return normalized_scope in self.scopes

    def resolved_description(self, state: dict[str, Any]) -> str:
        """Return the concrete description for this state."""
        if callable(self.description):
            return self.description(state)
        return self.description

    def resolved_parameters(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return the concrete JSON schema for this state."""
        if callable(self.parameters):
            return self.parameters(state)
        return self.parameters


WORKSPACE_ONLY = frozenset({AssistantToolScope.workspace})
CASE_ONLY = frozenset({AssistantToolScope.case})
BOTH_SCOPES = frozenset({AssistantToolScope.workspace, AssistantToolScope.case})
