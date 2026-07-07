"""Assistant configured-tool search and execution tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

from api_service.assistant.tools.catalog_execution import AssistantCatalogExecutor, CatalogToolCallArgs
from api_service.assistant.tools.definition import ToolDefinition
from api_service.assistant.tools.registration import BOTH_SCOPES, ScopedToolRegistration
from api_service.helpers import get_case_for_user
from api_service.runtime.execution import case_artifact_index_target
from api_service.runtime_tools.configured_tools import search_configured_tools
from backend_common.db import AssistantScope


class CatalogSearchArgs(BaseModel):
    query: str = Field(..., description="Natural-language description of the desired configured analysis tool or task.")
    top_k: int = Field(5, ge=1, le=20, description="Maximum number of matching configured tools to return.")


CATALOG_TOOL_REGISTRATIONS: tuple[ScopedToolRegistration, ...] = (
    ScopedToolRegistration(
        name="tool_search",
        description=(
            "Search configured NeuroCade runtime tools only. "
            "Use this before tool_call to find the configured container_id and tool_id."
        ),
        parameters=CatalogSearchArgs.model_json_schema(),
        handler_name="search",
        scopes=BOTH_SCOPES,
    ),
    ScopedToolRegistration(
        name="tool_call",
        description=(
            "Run a configured tool through a configured container. "
            "Pass the exact container_id and tool_id from tool_search. Use explicit "
            "/case/... paths for current-case files. Use the `bash` tool, not "
            "`tool_call`, for ad hoc shell commands."
        ),
        parameters=CatalogToolCallArgs.model_json_schema(),
        handler_name="call",
        scopes=BOTH_SCOPES,
    ),
)


class AssistantCatalogTools:
    def __init__(self, catalog_executor: AssistantCatalogExecutor) -> None:
        self.catalog_executor = catalog_executor

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for registration in CATALOG_TOOL_REGISTRATIONS:

            # skip if tool is not exposed in the current scope
            if not registration.exposed_in(str(state.get("scope") or "")):
                continue

            handler = getattr(self, registration.handler_name)
            async def execute(arguments: dict[str, Any], *, tool_handler=handler) -> str:
                return await asyncio.to_thread(tool_handler, state, arguments)

            definitions.append(
                ToolDefinition(
                    name=registration.name,
                    description=registration.resolved_description(state),
                    parameters=registration.resolved_parameters(state),
                    execute=execute,
                )
            )
        return definitions

    def call(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        binds = self.catalog_executor.catalog_runtime_binds(state)
        db = state.get("db")
        artifact_index_targets = ()
        if (
            state.get("scope") == AssistantScope.case.value
            and db is not None
            and state.get("context") is not None
            and state.get("workspace_id") is not None
            and state.get("case_id") is not None
        ):
            case, _role = get_case_for_user(
                db,
                state["case_id"],
                state["context"].user.id,
                workspace_id=state["workspace_id"],
            )
            artifact_index_targets = (case_artifact_index_target(case),)
        return self.catalog_executor.catalog_tool_call(
            arguments,
            binds,
            db=db,
            artifact_index_targets=artifact_index_targets,
        )

    def search(self, _state: dict[str, Any], arguments: dict[str, Any] | None = None) -> str:
        if arguments is None:
            arguments = _state
        parsed = CatalogSearchArgs.model_validate(arguments)
        try:
            hits = search_configured_tools(parsed.query, top_k=parsed.top_k)
        except Exception as exc:
            return f"Error searching configured tools: {exc}"

        payload: list[dict[str, Any]] = []
        for tool, container, score in hits:
            row: dict[str, Any] = {
                "tool_id": tool.id,
                "label": tool.label,
                "container_id": tool.container_id,
                "container_label": container.label if container else tool.container_id,
                "command": tool.command,
                "description": tool.description,
                "aliases": tool.aliases,
                "score": score,
            }
            payload.append(row)
        return json.dumps(payload, indent=2)
