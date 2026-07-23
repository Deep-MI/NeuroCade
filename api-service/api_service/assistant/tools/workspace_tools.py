"""Assistant workspace-level tool handlers."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.conversation_store import thread_key
from api_service.assistant.tools.case_tools import AssistantCaseTools
from api_service.assistant.tools.definition import ToolDefinition
from api_service.assistant.tools.registration import WORKSPACE_ONLY, ScopedToolRegistration
from api_service.workspace_batch import (
    cancel_workspace_batch_run,
    create_workspace_batch_run,
    create_workspace_command_run,
    get_workspace_batch_run_or_404,
    list_workspace_batch_runs,
    workspace_probe_bash,
)
from api_service.workspace_batch.filesystem import workspace_case_file_tree, workspace_case_mount_path, workspace_file_tree
from backend_common.auth import AuthContext
from backend_common.db import AssistantScope, Case, Run, Workspace, WorkspaceMembership
from backend_common.providers import ModelConfig

WORKSPACE_TOOL_REGISTRATIONS: tuple[ScopedToolRegistration, ...] = (
    ScopedToolRegistration(
        name="workspace_list_cases",
        description="List the cases currently available in this workspace, including internal case ids and external processing ids.",
        parameters={"type": "object", "properties": {}},
        handler_name="list_cases",
        scopes=WORKSPACE_ONLY,
    ),
    ScopedToolRegistration(
        name="workspace_case_file_tree",
        description="Show the full file tree for one case in the current workspace.",
        parameters={
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
        },
        handler_name="case_tree",
        scopes=WORKSPACE_ONLY,
    ),
    ScopedToolRegistration(
        name="workspace_file_tree",
        description="Show the selected workspace cases as they will be mounted for workspace analyses.",
        parameters={
            "type": "object",
            "properties": {"case_ids": {"type": "array", "items": {"type": "string"}}},
        },
        handler_name="file_tree",
        scopes=WORKSPACE_ONLY,
    ),
    ScopedToolRegistration(
        name="workspace_probe_bash",
        description="Run one bash command against a single case in the current workspace using a direct /case mount. Use this for help-text discovery and one-case dry runs before queueing workspace_batch_bash.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "case_id": {"type": "string"},
            },
            "required": ["command"],
        },
        handler_name="probe_bash",
        scopes=WORKSPACE_ONLY,
        requires_managed_bash=True,
    ),
    ScopedToolRegistration(
        name="workspace_bash",
        description="Queue one workspace-wide bash command over selected or all cases in the current workspace. Selected cases are mounted read-only under /cases/<case-slug>/ and outputs must be written under /workspace/. Use this for aggregate summaries, cohort metrics, or one-shot analyses that should produce workspace artifacts.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "case_ids": {"type": "array", "items": {"type": "string"}},
                "report_name": {"type": "string"},
            },
            "required": ["command"],
        },
        handler_name="bash",
        scopes=WORKSPACE_ONLY,
        requires_managed_bash=True,
    ),
    ScopedToolRegistration(
        name="workspace_batch_bash",
        description="Queue the same bash command across selected or all cases in the current workspace. Each case is mounted directly at /case inside its own background worker job. Before using this, inspect help first with workspace_probe_bash.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "case_ids": {"type": "array", "items": {"type": "string"}},
                "report_name": {"type": "string"},
            },
            "required": ["command"],
        },
        handler_name="batch_bash",
        scopes=WORKSPACE_ONLY,
        requires_managed_bash=True,
    ),
    ScopedToolRegistration(
        name="workspace_list_batch_runs",
        description="List recent background workspace runs.",
        parameters={"type": "object", "properties": {}},
        handler_name="list_batch_runs",
        scopes=WORKSPACE_ONLY,
    ),
    ScopedToolRegistration(
        name="workspace_cancel_batch_run",
        description="Cancel an in-progress workspace run.",
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        handler_name="cancel_batch_run",
        scopes=WORKSPACE_ONLY,
    ),
)


def workspace_case_rows(db: Session, user_id: str, workspace_id: str) -> list[Case]:
    return (
        db.query(Case)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Case.workspace_id)
        .filter(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
        .order_by(Case.title.asc())
        .all()
    )


class AssistantWorkspaceTools:
    def __init__(self, case_tools: AssistantCaseTools) -> None:
        self.case_tools = case_tools

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        managed_bash_available = self.case_tools.managed_bash_available()
        definitions: list[ToolDefinition] = []
        for registration in WORKSPACE_TOOL_REGISTRATIONS:
            if not registration.exposed_in(str(state.get("scope") or "")):
                continue
            if registration.requires_managed_bash and not managed_bash_available:
                continue
            handler = getattr(self, registration.handler_name)

            async def execute(
                arguments: dict[str, Any],
                *,
                tool_handler: Callable[[dict[str, Any], dict[str, Any]], str | Awaitable[str]] = handler,
            ) -> str:
                result = tool_handler(state, arguments)
                if inspect.isawaitable(result):
                    return await result
                return result

            definitions.append(
                ToolDefinition(
                    name=registration.name,
                    description=registration.resolved_description(state),
                    parameters=registration.resolved_parameters(state),
                    execute=execute,
                )
            )
        return definitions

    def case_summaries(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        if not db or not context or not workspace_id:
            return []
        summaries: list[dict[str, Any]] = []
        for case in workspace_case_rows(db, context.user.id, workspace_id):
            latest_run = (
                db.query(Run)
                .filter(Run.case_id == case.id)
                .order_by(Run.created_at.desc())
                .first()
            )
            summaries.append(
                {
                    "case_id": case.id,
                    "title": case.title,
                    "latest_run_status": latest_run.status.value if latest_run is not None else None,
                    "workspace_mount_path": workspace_case_mount_path(case),
                }
            )
        return summaries

    def require_context(self, state: dict[str, Any]) -> tuple[Session, AuthContext, Workspace]:
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        if not db or not context or not workspace_id:
            raise HTTPException(status_code=400, detail="Workspace tools require a persisted workspace context")

        workspace = db.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return db, context, workspace

    @staticmethod
    def provider_metadata(state: dict[str, Any]) -> tuple[str, str]:
        provider_config = state.get("provider_config")
        if isinstance(provider_config, ModelConfig):
            return provider_config.provider, provider_config.model
        return "unknown", "unknown"

    def case_tree(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        db, context, workspace = self.require_context(state)
        case_id = str(arguments.get("case_id") or "").strip()
        if not case_id:
            raise HTTPException(status_code=400, detail="workspace_case_file_tree requires case_id")
        return workspace_case_file_tree(db, context, workspace, case_id=case_id)

    def list_cases(self, state: dict[str, Any], _arguments: dict[str, Any] | None = None) -> str:
        return json.dumps(self.case_summaries(state), indent=2)

    def file_tree(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        db, context, workspace = self.require_context(state)
        case_ids = [case_id for case_id in arguments.get("case_ids", []) if isinstance(case_id, str)]
        return workspace_file_tree(db, context, workspace, case_ids=case_ids or None)

    async def probe_bash(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        db, context, workspace = self.require_context(state)
        command = str(arguments.get("command") or "").strip()
        case_id = str(arguments.get("case_id") or "").strip() or None
        if not command:
            raise HTTPException(status_code=400, detail="workspace_probe_bash requires command")
        return await workspace_probe_bash(db, context, workspace, command=command, case_id=case_id)

    def bash(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        db, context, workspace = self.require_context(state)
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="workspace_bash requires command")
        case_ids = [case_id for case_id in arguments.get("case_ids", []) if isinstance(case_id, str)]
        provider_name, model_name = self.provider_metadata(state)
        workspace_run = create_workspace_command_run(
            db,
            context,
            workspace,
            command=command,
            report_name=str(arguments.get("report_name") or "").strip() or None,
            case_ids=case_ids or None,
            thread_id=thread_key(scope=AssistantScope.workspace.value, workspace_id=workspace.id, case_id=None),
            provider_name=provider_name,
            model_name=model_name,
        )
        return (
            f"Queued workspace-wide run `{workspace_run.run_id}` for {workspace_run.selected_case_count} case(s).\n"
            f"Status: `{workspace_run.status}`.\n"
            "Selected cases will be mounted read-only at `/cases/<case-slug>/` and outputs should appear under `/workspace/` in the generated analysis folder."
        )

    def batch_bash(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        db, context, workspace = self.require_context(state)
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="workspace_batch_bash requires command")
        case_ids = [case_id for case_id in arguments.get("case_ids", []) if isinstance(case_id, str)]
        provider_name, model_name = self.provider_metadata(state)
        batch_run = create_workspace_batch_run(
            db,
            context,
            workspace,
            command=command,
            report_name=str(arguments.get("report_name") or "").strip() or None,
            case_ids=case_ids or None,
            thread_id=thread_key(scope=AssistantScope.workspace.value, workspace_id=workspace.id, case_id=None),
            provider_name=provider_name,
            model_name=model_name,
        )
        return (
            f"Queued workspace batch run `{batch_run.run_id}` for {batch_run.total_cases} case(s).\n"
            f"Status: `{batch_run.status}`.\n"
            "The first case is running as a probe. If it fails, all remaining cases will be canceled automatically."
        )

    def list_batch_runs(self, state: dict[str, Any], _arguments: dict[str, Any] | None = None) -> str:
        db, _context, workspace = self.require_context(state)
        runs = list_workspace_batch_runs(db, workspace.id)
        return json.dumps([run.model_dump(mode="json") for run in runs], indent=2)

    def cancel_batch_run(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        db, _context, workspace = self.require_context(state)
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise HTTPException(status_code=400, detail="workspace_cancel_batch_run requires run_id")
        parent_run = get_workspace_batch_run_or_404(db, workspace.id, run_id)
        detail = cancel_workspace_batch_run(db, parent_run)
        if detail.execution_mode == "workspace_wide":
            return f"Canceled workspace-wide run `{detail.run_id}`."
        return f"Canceled workspace batch run `{detail.run_id}`. {detail.canceled_cases} case(s) are now marked canceled."
