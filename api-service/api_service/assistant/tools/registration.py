"""Shared assistant tool registration metadata."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from api_service.assistant.approval_contracts import AssistantApprovalPresentation
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult, ToolRisk

ToolDescription = str | Callable[[dict[str, Any]], str]
ToolParameters = dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]
ToolHandler = Callable[
    [dict[str, Any], ToolExecutionContext, dict[str, Any]],
    ToolResult | Awaitable[ToolResult],
]
ToolApprovalPresenter = Callable[
    [dict[str, Any], dict[str, Any]],
    AssistantApprovalPresentation | None,
]


@dataclass(frozen=True)
class ToolRegistration:
    """Bind declarative tool metadata to one state-aware handler."""

    name: str
    description: ToolDescription
    parameters: ToolParameters
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.read
    parallel_safe: bool | None = None
    approval_presentation: ToolApprovalPresenter | None = None

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

    def bind(self, state: dict[str, Any]) -> ToolDefinition:
        presenter = self.approval_presentation

        async def execute(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
            result = self.handler(state, context, arguments)
            return await result if inspect.isawaitable(result) else result

        return ToolDefinition(
            name=self.name,
            description=self.resolved_description(state),
            parameters=self.resolved_parameters(state),
            execute=execute,
            risk=self.risk,
            parallel_safe=False if self.parallel_safe is None else self.parallel_safe,
            approval_presentation=(
                None
                if presenter is None
                else lambda arguments: presenter(state, arguments)
            ),
        )
