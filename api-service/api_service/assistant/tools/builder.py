"""Assistant tool registry construction.

This module composes the assistant's available tools from runtime GUI tools,
file tools, installed catalog tools, active-case container tools, and workspace
batch tools. The registry differs by assistant scope: workspace chat gets
workspace-safe tools, while case chat also gets GUI/runtime and case-mounted
execution tools.
"""

from __future__ import annotations

from typing import Any

from api_service.assistant.tools.catalog_tools import AssistantCatalogTools, CATALOG_TOOL_REGISTRATIONS
from api_service.assistant.tools.case_tools import AssistantCaseTools, CASE_TOOL_REGISTRATIONS
from api_service.assistant.tools.file_tools import AssistantFileTools, FILE_TOOL_REGISTRATIONS
from api_service.assistant.tools.catalog_execution import AssistantCatalogExecutor
from api_service.assistant.tools.definition import ToolDefinition
from api_service.assistant.tools.registration import ScopedToolRegistration
from api_service.assistant.tools.workspace_tools import AssistantWorkspaceTools, WORKSPACE_TOOL_REGISTRATIONS
from api_service.runtime.service import RuntimeService
from backend_common.db import AssistantScope
from backend_common.settings import ROOT_DIR


ASSISTANT_TOOL_REGISTRATION_GROUPS: tuple[tuple[str, tuple[ScopedToolRegistration, ...]], ...] = (
    ("workspace", WORKSPACE_TOOL_REGISTRATIONS),
    ("file", FILE_TOOL_REGISTRATIONS),
    ("case", CASE_TOOL_REGISTRATIONS),
    ("catalog", CATALOG_TOOL_REGISTRATIONS),
)
GUI_OVERRIDE_IDENTITY_KEYS = {
    "workspace_id",
    "current_workspace_id",
    "case_id",
    "current_case_id",
    "case_output_path",
    "current_case_output_path",
    "output_path",
}


class AssistantToolBuilder:
    """Build scope-aware assistant tool definitions and OpenAI tool specs."""

    def __init__(self, runtime_service: RuntimeService, *, settings: Any, root_dir=ROOT_DIR) -> None:
        """Create the helper objects that contribute assistant tools."""
        self.runtime_service = runtime_service
        self.catalog_executor = AssistantCatalogExecutor(settings=settings, root_dir=root_dir)
        self.case_tools = AssistantCaseTools(
            settings=settings,
            root_dir=root_dir,
            command_executor=self.catalog_executor,
        )
        self.file_tools = AssistantFileTools(settings=settings, runtime_service=runtime_service)
        self.catalog_tools = AssistantCatalogTools(self.catalog_executor)
        self.workspace_tools = AssistantWorkspaceTools(self.case_tools)

    async def build(self, state: dict[str, Any]) -> tuple[list[ToolDefinition], list[dict[str, Any]]]:
        """Build executable tool definitions and serialized model tool specs.

        Workspace scope combines workspace, file, and catalog tools. Case scope
        fetches GUI/runtime tools for the active GUI state and adds file,
        case-container, and catalog tools.
        """
        catalog_definitions = self.catalog_tools.build_tools(state)
        file_definitions = self.file_tools.build_tools(state)
        if state["scope"] == AssistantScope.workspace.value:
            definitions = [
                *self.workspace_tools.build_tools(state),
                *file_definitions,
                *catalog_definitions,
            ]
            return definitions, [definition.as_openai_tool() for definition in definitions]

        gui_state_key = self.gui_state_session_key(state)
        gui_state_override = self.authorized_gui_state_override(state)
        openai_tools = await self.runtime_service.fetch_tools(
            gui_state_key=gui_state_key,
            gui_state_override=gui_state_override,
        )
        definitions: list[ToolDefinition] = []
        for tool in openai_tools:
            function = tool.get("function", {})
            name = function.get("name", "")

            async def execute(arguments: dict[str, Any], *, tool_name: str = name) -> str:
                return await self.runtime_service.call_tool(
                    tool_name,
                    arguments,
                    gui_state_override=gui_state_override,
                    gui_state_key=gui_state_key,
                )

            definitions.append(
                ToolDefinition(
                    name=str(function.get("name", "")),
                    description=str(function.get("description", "")),
                    parameters=dict(function.get("parameters", {})),
                    execute=execute,
                )
            )
        definitions.extend(file_definitions)
        definitions.extend(self.case_tools.build_tools(state))
        definitions.extend(catalog_definitions)
        return definitions, [definition.as_openai_tool() for definition in definitions]

    async def load_gui_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return GUI state for case chat, merged with request-time overrides.

        Runtime GUI state is skipped for workspace scope. If fetching persisted
        GUI state fails, explicit overrides are still returned so callers can
        continue with the best available state snapshot.
        """
        if state["scope"] != AssistantScope.case.value:
            return {}
        gui_state_override = self.authorized_gui_state_override(state) or {}
        gui_state_key = self.gui_state_session_key(state)
        if gui_state_override and gui_state_key is None:
            return dict(gui_state_override)
        try:
            gui_state = await self.runtime_service.fetch_gui_state(gui_state_key=gui_state_key)
            return {**gui_state, **gui_state_override}
        except Exception:
            return dict(gui_state_override)

    def gui_state_session_key(self, state: dict[str, Any]) -> str | None:
        """Return the GUI-state session key derived from assistant state."""
        return self.file_tools.gui_state_session_key(state)

    @staticmethod
    def authorized_gui_state_override(state: dict[str, Any]) -> dict[str, Any] | None:
        """Return request GUI state with identity/path fields pinned to server scope."""
        raw_override = state.get("gui_state_override") or {}
        if not isinstance(raw_override, dict):
            raw_override = {}
        sanitized = {key: value for key, value in raw_override.items() if key not in GUI_OVERRIDE_IDENTITY_KEYS}
        if state.get("scope") == AssistantScope.case.value:
            sanitized["current_workspace_id"] = state.get("workspace_id")
            sanitized["current_case_id"] = state.get("case_id")
        elif state.get("scope") == AssistantScope.workspace.value:
            sanitized["current_workspace_id"] = state.get("workspace_id")
            sanitized["current_case_id"] = None
        return sanitized or None

    def case_summaries(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Return workspace case summaries for prompt and loop context."""
        return self.workspace_tools.case_summaries(state)
