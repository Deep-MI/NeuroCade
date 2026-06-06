"""Assistant file tools scoped to authorized NeuroCade storage roots.

The tools in this module let the assistant read, write, and edit UTF-8 text
files under the active workspace or case storage root. Paths are intentionally
limited to the authorized workspace and, for case-scoped chat, the active case
mounted as ``/case``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from api_service.assistant.tools.definition import ToolDefinition
from api_service.assistant.tools.registration import BOTH_SCOPES, ScopedToolRegistration
from api_service.gui_state import build_gui_state_session_key
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.runtime.service import RuntimeService
from backend_common.case_storage import case_storage_dir, workspace_storage_dir
from backend_common.db import AssistantScope


class ReadFileArgs(BaseModel):
    path: str = Field(..., description="Text file path under the active workspace or case root.")
    max_bytes: int = Field(100000, ge=1, le=500000, description="Maximum number of bytes to return.")


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Text file path under the active workspace or case root.")
    content: str = Field(..., description="Complete UTF-8 text content to write.")


class EditFileArgs(BaseModel):
    path: str = Field(..., description="Text file path under the active workspace or case root.")
    old_text: str = Field(..., description="Exact text to replace.")
    new_text: str = Field(..., description="Replacement text.")
    replace_all: bool = Field(False, description="Replace every occurrence instead of only the first one.")


def _read_description(state: dict[str, Any]) -> str:
    return f"Read a UTF-8 text file from the active NeuroCade data workspace. {AssistantFileTools.path_description(state)}"


def _write_description(state: dict[str, Any]) -> str:
    return f"Write a UTF-8 text file inside the active NeuroCade data workspace. {AssistantFileTools.path_description(state)}"


def _edit_description(state: dict[str, Any]) -> str:
    return f"Replace exact text inside a UTF-8 text file in the active NeuroCade data workspace. {AssistantFileTools.path_description(state)}"


def _read_schema(state: dict[str, Any]) -> dict[str, Any]:
    return AssistantFileTools.schema_with_path_description(ReadFileArgs, AssistantFileTools.path_description(state))


def _write_schema(state: dict[str, Any]) -> dict[str, Any]:
    return AssistantFileTools.schema_with_path_description(WriteFileArgs, AssistantFileTools.path_description(state))


def _edit_schema(state: dict[str, Any]) -> dict[str, Any]:
    return AssistantFileTools.schema_with_path_description(EditFileArgs, AssistantFileTools.path_description(state))


FILE_TOOL_REGISTRATIONS: tuple[ScopedToolRegistration, ...] = (
    ScopedToolRegistration(
        name="read",
        description=_read_description,
        parameters=_read_schema,
        handler_name="read_tool",
        scopes=BOTH_SCOPES,
    ),
    ScopedToolRegistration(
        name="write",
        description=_write_description,
        parameters=_write_schema,
        handler_name="write_tool",
        scopes=BOTH_SCOPES,
    ),
    ScopedToolRegistration(
        name="edit",
        description=_edit_description,
        parameters=_edit_schema,
        handler_name="edit_tool",
        scopes=BOTH_SCOPES,
    ),
)


class AssistantFileTools:
    """Build and resolve assistant file tools for NeuroCade-managed paths."""

    def __init__(self, *, settings, runtime_service: RuntimeService) -> None:
        """Store settings and runtime services used for path resolution."""
        self.settings = settings
        self.runtime_service = runtime_service

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        """Return read, write, and edit tool definitions for the current state.

        The path parameter descriptions are adjusted for workspace versus case
        scope so the model only sees ``/case`` advertised when a case is active.
        """
        definitions: list[ToolDefinition] = []
        scope = str(state.get("scope") or "")
        for registration in FILE_TOOL_REGISTRATIONS:
            if scope and not registration.exposed_in(scope):
                continue
            handler = getattr(self, registration.handler_name)

            async def execute(arguments: dict[str, Any], *, tool_handler=handler) -> str:
                return await tool_handler(state, arguments)

            definitions.append(
                ToolDefinition(
                    name=registration.name,
                    description=registration.resolved_description(state),
                    parameters=registration.resolved_parameters(state),
                    execute=execute,
                )
            )
        return definitions

    async def read_tool(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        """Read a UTF-8 text file after resolving an assistant-visible path."""
        parsed = ReadFileArgs.model_validate(arguments)
        path = await self.resolve_path(state, parsed.path)
        try:
            data = path.read_bytes()[: parsed.max_bytes]
        except OSError as exc:
            return f"Error reading {parsed.path}: {exc}"
        text = data.decode("utf-8", errors="replace")
        truncated = " [truncated]" if path.stat().st_size > parsed.max_bytes else ""
        return f"{path}\n{truncated}\n{text}"

    async def write_tool(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        """Write UTF-8 text after resolving an assistant-visible path."""
        parsed = WriteFileArgs.model_validate(arguments)
        path = await self.resolve_path(state, parsed.path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(parsed.content, encoding="utf-8")
        except OSError as exc:
            return f"Error writing {parsed.path}: {exc}"
        return f"Wrote {len(parsed.content.encode('utf-8'))} byte(s) to {path}."

    async def edit_tool(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        """Replace exact UTF-8 text after resolving an assistant-visible path."""
        parsed = EditFileArgs.model_validate(arguments)
        if not parsed.old_text:
            return "Error: old_text must not be empty."
        path = await self.resolve_path(state, parsed.path)
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error reading {parsed.path}: {exc}"
        count = original.count(parsed.old_text)
        if count == 0:
            return f"Error: old_text was not found in {path}."
        updated = (
            original.replace(parsed.old_text, parsed.new_text)
            if parsed.replace_all
            else original.replace(parsed.old_text, parsed.new_text, 1)
        )
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return f"Error writing {parsed.path}: {exc}"
        changed = count if parsed.replace_all else 1
        return f"Edited {path}; replaced {changed} occurrence(s)."

    @staticmethod
    def path_description(state: dict[str, Any]) -> str:
        """Describe the allowed assistant file path roots for this state."""
        if state.get("scope") == "case" and state.get("case_id"):
            return "Allowed paths are /case or paths relative to the active case directory."
        return (
            "Allowed paths are relative to the active workspace directory. /case is not available in workspace chat; "
            "use workspace_case_file_tree with a case_id to inspect a case, or open a case first."
        )

    @staticmethod
    def schema_with_path_description(model: type[BaseModel], path_description: str) -> dict[str, Any]:
        """Return a model JSON schema with the contextual path description."""
        schema = model.model_json_schema()
        properties = schema.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("path"), dict):
            properties["path"]["description"] = path_description
        return schema

    async def resolve_path(self, state: dict[str, Any], raw_path: str) -> Path:
        """Resolve an assistant-visible path to a host path under the data root.

        ``/case`` maps to the active case directory. Relative paths resolve
        under the active case or workspace directory. The final resolved path
        must remain inside an allowlisted root for the authenticated assistant
        scope.
        """
        raw = str(raw_path or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="File path must not be empty")
        allowed_roots = self.allowed_roots(state)

        if raw == "/case" or raw.startswith("/case/"):
            case_root = self.active_case_root(state)
            if case_root is None:
                raise HTTPException(status_code=400, detail="/case requires an active case")
            candidate = case_root / raw.removeprefix("/case").lstrip("/")
        elif raw.startswith("/"):
            raise HTTPException(status_code=400, detail="Only /case paths or relative paths are allowed")
        else:
            base = self.active_case_root(state) if state.get("scope") == AssistantScope.case.value else self.active_workspace_root(state)
            if base is None:
                raise HTTPException(status_code=400, detail="File tools require an authorized workspace or case context")
            candidate = base / raw

        resolved = candidate.resolve(strict=False)
        if not any(os.path.commonpath([str(resolved), str(allowed_root)]) == str(allowed_root) for allowed_root in allowed_roots):
            raise HTTPException(status_code=403, detail="File path is outside the authorized workspace or case")
        return resolved

    def allowed_roots(self, state: dict[str, Any]) -> tuple[Path, ...]:
        """Return canonical filesystem roots allowed for the assistant scope."""
        if state.get("scope") == AssistantScope.case.value:
            case_root = self.active_case_root(state)
            return (case_root,) if case_root is not None else ()
        workspace_root = self.active_workspace_root(state)
        return (workspace_root,) if workspace_root is not None else ()

    def active_workspace_root(self, state: dict[str, Any]) -> Path | None:
        """Resolve the authorized workspace storage directory from server state."""
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        if not db or not context or not workspace_id:
            return None
        workspace, _role = get_workspace_for_user(db, workspace_id, context.user.id)
        workspace_root = workspace_storage_dir(self.settings, workspace.id)
        workspace_root.mkdir(parents=True, exist_ok=True)
        return workspace_root.resolve()

    def active_case_root(self, state: dict[str, Any]) -> Path | None:
        """Resolve the authorized case storage directory from server state."""
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        case_id = state.get("case_id")
        if not db or not context or not workspace_id or not case_id:
            return None
        case, _role = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace_id)
        case_root = case_storage_dir(self.settings, str(workspace_id), str(case.id))
        case_root.mkdir(parents=True, exist_ok=True)
        return case_root.resolve()

    @staticmethod
    def gui_state_session_key(state: dict[str, Any]) -> str | None:
        """Build the runtime GUI-state key for the current assistant state."""
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        case_id = state.get("case_id")
        gui_session_id = state.get("gui_session_id")
        if not context or not workspace_id:
            return None
        return build_gui_state_session_key(
            user_id=context.user.id,
            workspace_id=workspace_id,
            case_id=case_id,
            gui_session_id=gui_session_id,
        )
