"""Assistant workspace-level tool handlers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from api_service.assistant.tools.registration import ToolRegistration
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.workspace_inspection import (
    workspace_case_container_path,
    workspace_case_file_tree,
    workspace_case_rows,
    workspace_file_tree,
)
from backend_common.auth import AuthContext
from backend_common.db import Run, Workspace


class AssistantWorkspaceTools:
    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        registrations = (
            ToolRegistration(
                "workspace_list_cases",
                "List the cases currently available in this workspace.",
                {"type": "object", "properties": {}},
                self.list_cases,
            ),
            ToolRegistration(
                "workspace_case_file_tree",
                "Show a bounded file tree for one case. Set path to inspect a specific directory such as mri or surf.",
                {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
                    },
                    "required": ["case_id"],
                },
                self.case_tree,
            ),
            ToolRegistration(
                "workspace_file_tree",
                "Show bounded trees for selected workspace cases as mounted for workspace analyses.",
                {
                    "type": "object",
                    "properties": {
                        "case_ids": {"type": "array", "items": {"type": "string"}},
                        "path": {"type": "string", "default": "."},
                        "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
                    },
                },
                self.file_tree,
            ),
        )
        return [registration.bind(state) for registration in registrations]

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
                    "workspace_path": workspace_case_container_path(case),
                }
            )
        return summaries

    def require_context(self, state: dict[str, Any]) -> tuple[Session, AuthContext, Workspace]:
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        if not db or not context or not workspace_id:
            raise HTTPException(status_code=400, detail="Workspace tools require a persisted workspace context")

        workspace, _role = get_workspace_for_user(db, workspace_id, context.user.id)
        return db, context, workspace

    def case_tree(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        db, context, workspace = self.require_context(state)
        case_id = str(arguments.get("case_id") or "").strip()
        if not case_id:
            raise HTTPException(status_code=400, detail="workspace_case_file_tree requires case_id")
        return ToolResult.success(
            workspace_case_file_tree(
                db,
                context,
                workspace,
                case_id=case_id,
                path=str(arguments.get("path") or "."),
                max_entries=max(1, min(int(arguments.get("max_entries") or 500), 500)),
            )
        )

    def list_cases(
        self,
        state: dict[str, Any],
        _execution: ToolExecutionContext,
        _arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult.success(json.dumps(self.case_summaries(state), indent=2))

    def file_tree(
        self, state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        db, context, workspace = self.require_context(state)
        case_ids = [case_id for case_id in arguments.get("case_ids", []) if isinstance(case_id, str)]
        return ToolResult.success(
            workspace_file_tree(
                db,
                context,
                workspace,
                case_ids=case_ids or None,
                path=str(arguments.get("path") or "."),
                max_entries=max(1, min(int(arguments.get("max_entries") or 500), 500)),
            )
        )

    def build_case_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        """Return metadata inspection tools for the active case."""
        registration = ToolRegistration(
            "case_info",
            "Return authoritative metadata for the active case, including its title, notes, tags, and workspace.",
            {"type": "object", "properties": {}},
            self.case_info,
        )
        return [registration.bind(state)]

    def case_info(
        self,
        state: dict[str, Any],
        _execution: ToolExecutionContext,
        _arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        case_id = state.get("case_id")
        if not db or not context or not workspace_id or not case_id:
            raise HTTPException(status_code=400, detail="case_info requires an active case")
        case, workspace, _role, _root = get_case_for_user(
            db, case_id, context.user.id, workspace_id=workspace_id
        )
        return ToolResult.success(
            json.dumps(
                {
                    "case_id": case.id,
                    "title": case.title,
                    "description": case.description,
                    "modalities": list(case.modalities_json or []),
                    "tags": list(case.tags_json or []),
                    "notes": case.notes,
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                },
                indent=2,
            )
        )
