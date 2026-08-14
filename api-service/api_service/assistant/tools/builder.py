"""Assistant tool registry construction.

This module composes the assistant's available tools from runtime GUI tools,
file tools, configured workflows, and workspace inspection tools. The registry
differs by assistant scope: workspace chat gets workspace-safe tools, while case
chat also gets GUI/runtime tools.
"""

from __future__ import annotations

from typing import Any

from api_service.assistant.tools.catalog_execution import AssistantCatalogExecutor
from api_service.assistant.tools.catalog_tools import AssistantCatalogTools
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult, ToolRisk
from api_service.assistant.tools.file_tools import AssistantFileTools
from api_service.assistant.tools.image_tools import AssistantImageTools
from api_service.assistant.tools.probe_tools import AssistantProbeTools
from api_service.assistant.tools.workspace_tools import AssistantWorkspaceTools
from api_service.runtime.gui_runtime import GuiRuntime
from backend_common.db import AssistantScope

GUI_OVERRIDE_DISPLAY_KEYS = {
    "is_job_running",
    "current_intensity_artifact_id",
    "current_intensity_volume",
    "current_cursor",
    "layers",
}


class AssistantToolBuilder:
    """Build scope-aware assistant tool definitions and OpenAI tool specs."""

    def __init__(self, gui_runtime: GuiRuntime, *, settings: Any) -> None:
        """Create the helper objects that contribute assistant tools."""
        self.gui_runtime = gui_runtime
        self.catalog_executor = AssistantCatalogExecutor(settings=settings)
        self.file_tools = AssistantFileTools(settings=settings)
        self.image_tools = AssistantImageTools(settings=settings)
        self.probe_tools = AssistantProbeTools(settings=settings)
        self.catalog_tools = AssistantCatalogTools(self.catalog_executor)
        self.workspace_tools = AssistantWorkspaceTools()

    def build(self, state: dict[str, Any]) -> tuple[list[ToolDefinition], list[dict[str, Any]]]:
        """Build executable tool definitions and serialized model tool specs.

        Workspace scope combines workspace, file, and catalog tools. Case scope
        fetches GUI/runtime tools for the active GUI state and adds file and
        catalog tools.
        """
        catalog_definitions = self.catalog_tools.build_tools(state)
        image_definitions = self.image_tools.build_tools(state)
        probe_definitions = self.probe_tools.build_tools(state)
        file_definitions = self.file_tools.build_tools(state)
        if state["scope"] == AssistantScope.workspace.value:
            definitions = [
                *self.workspace_tools.build_tools(state),
                *file_definitions,
                *image_definitions,
                *probe_definitions,
                *catalog_definitions,
            ]
            return definitions, [definition.as_openai_tool() for definition in definitions]

        gui_state_key = self.gui_state_session_key(state)
        gui_state_override = self.authorized_gui_state_override(state)
        openai_tools = self.gui_runtime.available_tools(
            gui_state_key=gui_state_key,
            gui_state_override=gui_state_override,
        )
        definitions: list[ToolDefinition] = []
        for tool in openai_tools:
            function = tool.get("function", {})
            name = function.get("name", "")

            async def execute(
                _execution: ToolExecutionContext,
                arguments: dict[str, Any],
                *,
                tool_name: str = name,
            ) -> ToolResult:
                return ToolResult.success(
                    self.gui_runtime.call_tool(
                        tool_name,
                        arguments,
                        gui_state_override=gui_state_override,
                        gui_state_key=gui_state_key,
                    )
                )

            definitions.append(
                ToolDefinition(
                    name=str(function.get("name", "")),
                    description=str(function.get("description", "")),
                    parameters=dict(function.get("parameters", {})),
                    execute=execute,
                    risk=ToolRisk.gui,
                )
            )
        definitions.extend(file_definitions)
        definitions.extend(self.workspace_tools.build_case_tools(state))
        definitions.extend(image_definitions)
        definitions.extend(probe_definitions)
        definitions.extend(catalog_definitions)
        return definitions, [definition.as_openai_tool() for definition in definitions]

    def load_gui_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return GUI state for case chat, merged with request-time overrides.

        Runtime GUI state is skipped for workspace scope. If fetching persisted
        GUI state fails, explicit overrides are still returned so callers can
        continue with the best available state snapshot.
        """
        if state["scope"] != AssistantScope.case.value:
            return {}
        gui_state_override = self.authorized_gui_state_override(state) or {}
        gui_state_key = self.gui_state_session_key(state)
        try:
            gui_state = self.gui_runtime.gui_state(gui_state_key=gui_state_key)
            return {**gui_state, **gui_state_override}
        except Exception:
            return dict(gui_state_override)

    def gui_state_session_key(self, state: dict[str, Any]) -> str:
        """Return the GUI-state session key derived from assistant state."""
        return self.file_tools.gui_state_session_key(state)

    @staticmethod
    def authorized_gui_state_override(state: dict[str, Any]) -> dict[str, Any] | None:
        """Return request GUI state with identity/path fields pinned to server scope."""
        raw_override = state.get("gui_state_override") or {}
        if not isinstance(raw_override, dict):
            raw_override = {}
        sanitized = {
            key: value
            for key, value in raw_override.items()
            if key in GUI_OVERRIDE_DISPLAY_KEYS
        }
        sanitized["workspace_id"] = state.get("workspace_id")
        sanitized["case_id"] = state.get("case_id") if state.get("scope") == AssistantScope.case.value else None
        return sanitized or None

    def case_summaries(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Return workspace case summaries for prompt and loop context."""
        return self.workspace_tools.case_summaries(state)
