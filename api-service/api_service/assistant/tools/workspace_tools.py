"""Assistant workspace-level tool handlers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
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
from backend_common.db import Case, Run, Workspace


class AssistantWorkspaceTools:
    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        registrations = (
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

    def case_summaries(self, state: dict[str, Any], cases=None) -> list[dict[str, Any]]:
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        if not db or not context or not workspace_id:
            return []
        cases = workspace_case_rows(db, context.user.id, workspace_id) if cases is None else cases
        ranked = db.query(Run.case_id, Run.status, func.row_number().over(
            partition_by=Run.case_id, order_by=(Run.created_at.desc(), Run.id.desc()),
        ).label("position")).filter(Run.case_id.in_([case.id for case in cases])).subquery()
        statuses = dict(db.query(ranked.c.case_id, ranked.c.status).filter(ranked.c.position == 1).all())
        summaries: list[dict[str, Any]] = []
        for case in cases:
            status = statuses.get(case.id)
            summaries.append(
                {
                    "case_id": case.id,
                    "title": case.title,
                    "latest_run_status": status.value if status is not None else None,
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
        db, _context, workspace = self.require_context(state)
        arguments = _arguments or {}
        limit = arguments.get("limit", 20)
        query = db.query(Case).filter(Case.workspace_id == workspace.id).order_by(Case.id)
        if arguments.get("after"):
            query = query.filter(Case.id > arguments["after"])
        cases = query.limit(limit + 1).all()
        return ToolResult.structured({"cases": self.case_summaries(state, cases[:limit]),
                                      "next_cursor": cases[limit - 1].id if len(cases) > limit else None})

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

    def build_inspection_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        """Return canonical case listing and metadata tools for either assistant scope."""
        registration = ToolRegistration(
            "get_case",
            "Return case metadata, including title, notes, tags, and workspace. "
            "In workspace scope, supply a case_id from list_cases; otherwise defaults to the active case.",
            {"type": "object", "properties": {
                "case_id": {"type": "string", "minLength": 1, "maxLength": 255},
            }, "additionalProperties": False},
            self.case_info,
        )
        listing = ToolRegistration(
            "list_cases",
            "List a page of workspace cases with latest run status and workspace path.",
            {"type": "object", "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "after": {"type": "string", "maxLength": 255},
            }, "additionalProperties": False},
            self.list_cases,
        )
        return [listing.bind(state), registration.bind(state)]

    def case_info(
        self,
        state: dict[str, Any],
        _execution: ToolExecutionContext,
        _arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        active_case_id = state.get("case_id")
        case_id = (_arguments or {}).get("case_id") or active_case_id
        if active_case_id and case_id != active_case_id:
            raise HTTPException(status_code=404, detail="Case not found")
        if not db or not context or not workspace_id or not case_id:
            raise HTTPException(status_code=400, detail="Supply case_id from list_cases or open a case first")
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
