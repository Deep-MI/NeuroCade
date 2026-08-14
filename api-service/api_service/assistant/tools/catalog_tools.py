"""Assistant configured-tool search and execution tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

from api_service.assistant.tools.catalog_execution import (
    AssistantCatalogExecutor,
    CatalogRunArgs,
    CatalogRunListArgs,
    CatalogToolCallArgs,
)
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult, ToolRisk
from api_service.assistant.tools.registration import ToolRegistration
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.policies import require_case_write, require_workspace_write
from api_service.runtime_tools.neurodesk_images import resolve_or_prepare_image
from api_service.runtime_tools.workflow_catalog import (
    NeuroimagingWorkflow,
    delete_user_workflow,
    inspect_workflow,
    resolve_workflow,
    search_workflows,
    upsert_user_workflow,
    workflow_source,
)
from backend_common.db import AssistantScope


class CatalogSearchArgs(BaseModel):
    query: str = Field(..., description="Natural-language description of the desired catalog workflow or task.")
    top_k: int = Field(5, ge=1, le=20, description="Maximum number of matching catalog workflows to return.")


class CatalogConfigGetArgs(BaseModel):
    tool_id: str = Field(..., description="Exact workflow id to read from the effective user catalog.")


class CatalogConfigUpsertArgs(BaseModel):
    definition: dict[str, Any] = Field(
        ...,
        description=(
            "Complete workflow definition to create or replace. Use only documented CLI flags. "
            "The script must use quoted ${INPUTS[n]} and should write declared files to quoted "
            "${OUTPUTS[n]}. For a FreeSurfer-LUT segmentation, set output metadata.lut to "
            "freesurfer and metadata.visible to true. Do not add optional command flags that "
            "the user did not request."
        ),
    )


def _inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local Pydantic references for tool providers that mishandle nested refs."""
    definitions = schema.get("$defs", {})

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return expand(definitions[reference.removeprefix("#/$defs/")])
            return {key: expand(item) for key, item in value.items() if key != "$defs"}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(schema)


def _catalog_config_upsert_schema() -> dict[str, Any]:
    definition_schema = _inline_schema(NeuroimagingWorkflow.model_json_schema(by_alias=True))
    definition_schema["description"] = CatalogConfigUpsertArgs.model_fields["definition"].description
    return {
        "type": "object",
        "properties": {"definition": definition_schema},
        "required": ["definition"],
        "additionalProperties": False,
    }


class CatalogConfigDeleteArgs(BaseModel):
    tool_id: str = Field(..., description="Exact private workflow or override id to delete.")


class AssistantCatalogTools:
    def __init__(self, catalog_executor: AssistantCatalogExecutor) -> None:
        self.catalog_executor = catalog_executor

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        registrations: tuple[ToolRegistration, ...] = (
            ToolRegistration(
                "tool_search",
                "Search the NeuroCade neuroimaging workflow catalog. Use tool_inspect on the selected result before tool_call.",
                CatalogSearchArgs.model_json_schema(),
                self.search,
                parallel_safe=True,
            ),
            ToolRegistration(
                "tool_inspect",
                "Load the extended guidance, ordered inputs, outputs, and execution contract for one configured workflow.",
                {
                    "type": "object",
                    "properties": {"tool_id": {"type": "string", "description": "Exact tool id returned by tool_search."}},
                    "required": ["tool_id"],
                    "additionalProperties": False,
                },
                self.inspect,
                parallel_safe=True,
            ),
            ToolRegistration(
                "tool_call",
                "Run one fixed catalog workflow using its exact tool_id and ordered /case/... or /workspace/... inputs.",
                CatalogToolCallArgs.model_json_schema(),
                self.call,
                ToolRisk.workflow,
            ),
            ToolRegistration(
                "tool_run_status",
                "Get the current status, final result, or failure from a workflow run.",
                CatalogRunArgs.model_json_schema(),
                self.status,
            ),
            ToolRegistration(
                "tool_run_list",
                "List recent workflow runs in the active case or workspace. Returns the last 10 unless limit is specified.",
                CatalogRunListArgs.model_json_schema(),
                self.list_runs,
                parallel_safe=True,
            ),
            ToolRegistration(
                "tool_run_cancel",
                "Cancel a queued or running workflow.",
                CatalogRunArgs.model_json_schema(),
                self.cancel,
                ToolRisk.workflow,
            ),
        )
        if self.user_id(state) is not None:
            registrations += (
                ToolRegistration(
                    "tool_config_get",
                    "Read the complete effective workflow definition, including its script and source.",
                    CatalogConfigGetArgs.model_json_schema(),
                    self.config_get,
                    parallel_safe=True,
                ),
                ToolRegistration(
                    "tool_config_upsert",
                    "Create or replace one workflow in the authenticated user's private catalog overlay.",
                    _catalog_config_upsert_schema(),
                    self.config_upsert,
                    ToolRisk.write,
                ),
                ToolRegistration(
                    "tool_config_delete",
                    "Delete one workflow from the authenticated user's private overlay. Built-in workflows are never deleted.",
                    CatalogConfigDeleteArgs.model_json_schema(),
                    self.config_delete,
                    ToolRisk.write,
                ),
            )
        return [registration.bind(state) for registration in registrations]

    @staticmethod
    def user_id(state: dict[str, Any]) -> str | None:
        context = state.get("context")
        user = getattr(context, "user", None)
        user_id = getattr(user, "id", None)
        return str(user_id) if user_id else None

    async def call(
        self, state: dict[str, Any], execution_context: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            parsed = CatalogToolCallArgs.model_validate(arguments)
            user_id = self.user_id(state)
            tool = resolve_workflow(
                parsed.tool_id,
                settings=self.catalog_executor.settings,
                user_id=user_id,
            )
            await asyncio.to_thread(
                resolve_or_prepare_image,
                tool.neurodesk_image,
                settings=self.catalog_executor.settings,
            )
        except Exception as exc:
            return ToolResult.error(f"Error preparing tool image: {exc}")
        binds = self.catalog_executor.catalog_runtime_binds(state)
        db = state.get("db")
        result = self.catalog_executor.catalog_tool_call(
            arguments,
            binds,
            db=db,
            user_id=user_id,
            workspace_id=state.get("workspace_id"),
            case_id=state.get("case_id"),
            scope=str(state.get("scope") or AssistantScope.case.value),
            run_id=execution_context.external_run_id,
        )
        if result.is_error:
            return result
        queued = result.details
        execution = queued.get("execution") if isinstance(queued, dict) else None
        run_id = queued.get("run_id") if isinstance(queued, dict) else None
        if (
            not isinstance(execution, dict)
            or execution.get("mode") != "synchronous"
            or not isinstance(run_id, str)
            or db is None
        ):
            return result

        completed = await self.catalog_executor.wait_for_terminal_run(
            db,
            run_id=run_id,
            workspace_id=state["workspace_id"],
            case_id=state.get("case_id") if state.get("scope") == AssistantScope.case.value else None,
        )
        return completed or result

    def search(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        parsed = CatalogSearchArgs.model_validate(arguments)
        try:
            user_id = self.user_id(state)
            hits = search_workflows(
                parsed.query,
                top_k=parsed.top_k,
                settings=self.catalog_executor.settings,
                user_id=user_id,
            )
        except Exception as exc:
            return ToolResult.error(f"Error searching workflow catalog: {exc}")

        payload: list[dict[str, Any]] = []
        for tool, score in hits:
            row: dict[str, Any] = {
                "tool_id": tool.id,
                "label": tool.label,
                "description": tool.description,
                "score": score,
            }
            payload.append(row)
        return ToolResult.success(json.dumps(payload, indent=2), details={"matches": payload})

    def inspect(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            tool_id = str(arguments.get("tool_id") or "").strip()
            user_id = self.user_id(state)
            payload = inspect_workflow(
                tool_id,
                settings=self.catalog_executor.settings,
                user_id=user_id,
            )
            payload["source"] = workflow_source(
                tool_id,
                settings=self.catalog_executor.settings,
                user_id=user_id,
            )
            return ToolResult.success(json.dumps(payload, indent=2), details=payload)
        except Exception as exc:
            return ToolResult.error(f"Error inspecting workflow: {exc}")

    def config_get(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            user_id = self.user_id(state)
            if user_id is None:
                raise ValueError("Workflow configuration requires an authenticated user.")
            parsed = CatalogConfigGetArgs.model_validate(arguments)
            tool = resolve_workflow(
                parsed.tool_id,
                settings=self.catalog_executor.settings,
                user_id=user_id,
            )
            payload = {
                    "source": workflow_source(
                        tool.id,
                        settings=self.catalog_executor.settings,
                        user_id=user_id,
                    ),
                    "definition": tool.model_dump(mode="json", by_alias=True, exclude_none=True),
                }
            return ToolResult.success(json.dumps(payload, indent=2), details=payload)
        except Exception as exc:
            return ToolResult.error(f"Error reading workflow configuration: {exc}")

    def config_upsert(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            user_id = self.user_id(state)
            if user_id is None:
                raise ValueError("Workflow configuration requires an authenticated user.")
            parsed = CatalogConfigUpsertArgs.model_validate(arguments)
            tool = upsert_user_workflow(self.catalog_executor.settings, user_id, parsed.definition)
            payload = {
                    "status": "reloaded",
                    "source": workflow_source(
                        tool.id,
                        settings=self.catalog_executor.settings,
                        user_id=user_id,
                    ),
                    "definition": tool.model_dump(mode="json", by_alias=True, exclude_none=True),
                }
            return ToolResult.success(json.dumps(payload, indent=2), details=payload)
        except Exception as exc:
            return ToolResult.error(f"Error updating workflow configuration: {exc}")

    def config_delete(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            user_id = self.user_id(state)
            if user_id is None:
                raise ValueError("Workflow configuration requires an authenticated user.")
            parsed = CatalogConfigDeleteArgs.model_validate(arguments)
            removed = delete_user_workflow(self.catalog_executor.settings, user_id, parsed.tool_id)
            try:
                effective = resolve_workflow(
                    parsed.tool_id,
                    settings=self.catalog_executor.settings,
                    user_id=user_id,
                )
            except ValueError:
                effective = None
            payload = {
                    "status": "reloaded",
                    "deleted_tool_id": removed.id,
                    "effective_definition": (
                        effective.model_dump(mode="json", by_alias=True, exclude_none=True)
                        if effective is not None
                        else None
                    ),
                    "effective_source": (
                        workflow_source(
                            parsed.tool_id,
                            settings=self.catalog_executor.settings,
                            user_id=user_id,
                        )
                        if effective is not None
                        else None
                    ),
                }
            return ToolResult.success(json.dumps(payload, indent=2), details=payload)
        except Exception as exc:
            return ToolResult.error(f"Error deleting workflow configuration: {exc}")

    def status(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        parsed = CatalogRunArgs.model_validate(arguments)
        if state.get("db") is None or state.get("workspace_id") is None:
            return ToolResult.error("Error: workflow status requires an active workspace.")
        return self.catalog_executor.run_status(
            state["db"],
            run_id=parsed.run_id,
            workspace_id=state["workspace_id"],
            case_id=state.get("case_id") if state.get("scope") == AssistantScope.case.value else None,
        )

    def list_runs(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        parsed = CatalogRunListArgs.model_validate(arguments)
        if state.get("db") is None or state.get("workspace_id") is None:
            return ToolResult.error("Error: listing workflow runs requires an active workspace.")
        return self.catalog_executor.list_runs(
            state["db"],
            workspace_id=state["workspace_id"],
            limit=parsed.limit,
            case_id=state.get("case_id") if state.get("scope") == AssistantScope.case.value else None,
        )

    def cancel(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        parsed = CatalogRunArgs.model_validate(arguments)
        if state.get("db") is None or state.get("workspace_id") is None:
            return ToolResult.error("Error: workflow cancellation requires an active workspace.")
        context = state.get("context")
        if context is None:
            return ToolResult.error("Error: workflow cancellation requires an authenticated user.")
        if state.get("scope") == AssistantScope.case.value:
            case_id = state.get("case_id")
            if not case_id:
                return ToolResult.error("Error: workflow cancellation requires an active case.")
            _case, _workspace, role, _case_dir = get_case_for_user(
                state["db"], case_id, context.user.id, workspace_id=state["workspace_id"]
            )
            require_case_write(role)
        else:
            _workspace, role = get_workspace_for_user(state["db"], state["workspace_id"], context.user.id)
            require_workspace_write(role)
        return self.catalog_executor.cancel_run(
            state["db"],
            run_id=parsed.run_id,
            workspace_id=state["workspace_id"],
            case_id=state.get("case_id") if state.get("scope") == AssistantScope.case.value else None,
        )
