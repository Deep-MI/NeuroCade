"""Assistant catalog search and execution tools."""

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
from backend_common.db import AssistantScope


MAX_HELP_TEXT_CHARS = 50_000


class CatalogSearchArgs(BaseModel):
    query: str = Field(..., description="Natural-language description of the desired neuroimaging tool or task.")
    top_k: int = Field(5, ge=1, le=20, description="Maximum number of matching installed tools to return.")
    records_jsonl: str | None = Field(None, description="Optional path to an installed tool index JSONL file.")


CATALOG_TOOL_REGISTRATIONS: tuple[ScopedToolRegistration, ...] = (
    ScopedToolRegistration(
        name="tool_search",
        description=(
            "Search installed neuroimaging runtime tools. "
            "Use this before choosing unfamiliar command names or flags."
        ),
        parameters=CatalogSearchArgs.model_json_schema(),
        handler_name="search",
        scopes=BOTH_SCOPES,
    ),
    ScopedToolRegistration(
        name="tool_call",
        description=(
            "Run an installed container tool through the resolved container runtime. "
            "Use explicit /case/... paths for current-case files. Use the `bash` tool, "
            "not `tool_call`, for shell commands."
        ),
        parameters=CatalogToolCallArgs.model_json_schema(),
        handler_name="call",
        scopes=BOTH_SCOPES,
    ),
)


def bounded_help_text(hit: dict[str, Any], *, max_chars: int = MAX_HELP_TEXT_CHARS) -> str:
    help_text = str(hit.get("raw_help_text") or "")
    if len(help_text) <= max_chars:
        return help_text
    return help_text[:max_chars].rstrip() + "\n[help_text truncated]"


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
        records_jsonl = self.catalog_executor.catalog_records_path(parsed.model_dump())
        if not records_jsonl.exists():
            return (
                f"Error: installed tool index not found at {records_jsonl}. "
                "Run ./scripts/compose/images.sh or restart the Compose stack."
            )
        try:
            from neurocade_runtime_tools.retrieval import hybrid_rank, load_jsonl_records

            records = load_jsonl_records(records_jsonl)
            hits = hybrid_rank(parsed.query, records, n_results=parsed.top_k)
        except Exception as exc:
            return f"Error searching tool catalog: {exc}"

        payload: list[dict[str, Any]] = []
        for hit in hits:
            row: dict[str, Any] = {
                "name": hit.get("name"),
                "source_package": hit.get("app"),
                "help_text": bounded_help_text(hit),
            }
            payload.append(row)
        return json.dumps(payload, indent=2)
