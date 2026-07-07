"""Configured-tool execution for assistant tools."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.runtime.execution import execute_runtime_request
from api_service.runtime_tools.case_resolver import CONTAINER_CASE_ROOT, resolve_case_mount_from_db
from api_service.runtime_tools.configured_tools import ConfiguredContainer, ConfiguredTool, resolve_configured_tool
from backend_common.case_storage import workspace_storage_dir
from backend_common.db import AssistantScope
from neurocade_runtime_tools.container_request import RuntimeBind, build_container_request
from neurocade_runtime_tools.execution import RuntimeArtifactIndexTarget, RuntimeContainerRunRequest, RuntimeExecutionPolicy, RuntimeExecutionRequest


class CatalogToolCallArgs(BaseModel):
    container_id: str = Field(..., description="Configured container id returned by tool_search.")
    tool_id: str = Field(..., description="Configured tool id or alias returned by tool_search.")
    tool_args: list[str] = Field(default_factory=list, description="Command-line arguments to pass to the tool.")


class AssistantCatalogExecutor:
    """Resolve and execute configured tools for assistant requests."""

    def __init__(self, *, settings, root_dir: Path) -> None:
        """Store runtime settings and the project root used for execution."""
        self.settings = settings
        self.root_dir = root_dir

    def catalog_runtime_binds(self, state: dict[str, Any]) -> list[RuntimeBind]:
        """Return container bind mounts appropriate for the assistant scope.

        Workspace-scoped calls mount only the authorized workspace storage
        directory at ``/workspace``. Case-scoped calls resolve the active case
        from the database and mount only that case read-write at ``/case``.
        """
        if state.get("scope") != AssistantScope.case.value:
            db = state.get("db")
            context = state.get("context")
            workspace_id = state.get("workspace_id")
            if db is None or context is None or workspace_id is None:
                return []
            workspace, _role = get_workspace_for_user(db, workspace_id, context.user.id)
            workspace_root = workspace_storage_dir(self.settings, workspace.id)
            workspace_root.mkdir(parents=True, exist_ok=True)
            return [RuntimeBind(workspace_root, "/workspace", "rw")]

        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        case_id = state.get("case_id")
        if db is None or context is None or workspace_id is None or case_id is None:
            return []

        case, _role = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace_id)
        workspace, _workspace_role = get_workspace_for_user(db, workspace_id, context.user.id)
        case_dir = resolve_case_mount_from_db(db, self.settings, case, workspace)
        return [RuntimeBind(case_dir, CONTAINER_CASE_ROOT, "rw")] if case_dir is not None else []

    def catalog_tool_call(
        self,
        arguments: dict[str, Any],
        binds: list[RuntimeBind] | None = None,
        *,
        db=None,
        artifact_index_targets: tuple[RuntimeArtifactIndexTarget, ...] = (),
    ) -> str:
        """Resolve a configured tool request, execute it, and return JSON output.

        ``tool_args`` may be supplied as a shell-like string or list. The
        rendered response includes public execution metadata plus bounded
        stdout/stderr tails from the runtime command.
        """
        raw_args = dict(arguments)
        if isinstance(raw_args.get("tool_args"), str):
            raw_args["tool_args"] = shlex.split(str(raw_args["tool_args"]))
        parsed = CatalogToolCallArgs.model_validate(raw_args)
        try:
            container, tool = resolve_configured_tool(parsed.container_id, parsed.tool_id)
            command = build_container_request(
                image=container.image,
                command=[tool.command, *parsed.tool_args],
                binds=binds or [],
                cwd=self.container_cwd_for_binds(binds or []),
                disable_network=True,
                gpu=False,
            )
        except Exception as exc:
            return f"Error preparing tool execution: {exc}"

        public_execution = self.public_catalog_execution(container, tool, parsed.tool_id, parsed.tool_args)
        timeout_s = int(os.environ.get("NEURO_CLI_TOOL_TIMEOUT", "300"))
        try:
            execute_kwargs: dict[str, Any] = {"timeout_s": timeout_s}
            if db is not None:
                execute_kwargs["db"] = db
            if artifact_index_targets:
                execute_kwargs["artifact_index_targets"] = artifact_index_targets
            completed = self.execute_runtime_command(command, **execute_kwargs)
        except Exception as exc:
            return f"Error executing catalog tool: {exc}"

        return json.dumps(
            {
                "execution": public_execution,
                "returncode": completed["returncode"],
                "stdout": completed["stdout"][-20000:],
                "stderr": completed["stderr"][-20000:],
                "execution_backend": completed["execution_backend"],
            },
            indent=2,
        )

    def execute_runtime_command(
        self,
        command: RuntimeContainerRunRequest,
        *,
        timeout_s: int,
        db=None,
        artifact_index_targets: tuple[RuntimeArtifactIndexTarget, ...] = (),
    ) -> dict[str, Any]:
        """Execute a prepared container runtime command in-process."""
        result = execute_runtime_request(
            RuntimeExecutionRequest(
                argv=[],
                cwd=self.root_dir,
                timeout_s=timeout_s,
                execution_mode="container",
                runtime_policy=RuntimeExecutionPolicy(
                    network_disabled=True,
                    gpu_enabled=command.gpu_enabled,
                ),
                artifact_index_targets=artifact_index_targets,
                container_run=command,
            ),
            db=db,
        )
        return result.as_dict()

    @staticmethod
    def container_cwd_for_binds(binds: list[RuntimeBind]) -> str | None:
        """Choose the natural working directory for a Docker catalog request."""
        if any(bind.container_path.rstrip("/") == CONTAINER_CASE_ROOT for bind in binds):
            return CONTAINER_CASE_ROOT
        if any(bind.container_path.rstrip("/") == "/workspace" for bind in binds):
            return "/workspace"
        return None

    @staticmethod
    def public_catalog_execution(
        container: ConfiguredContainer,
        tool: ConfiguredTool,
        requested_tool_id: str,
        tool_args: list[str],
    ) -> dict[str, Any]:
        """Return non-sensitive execution metadata for assistant/tool logs."""
        return {
            "tool_id": tool.id,
            "requested_tool_id": requested_tool_id,
            "container_id": container.id,
            "container_label": container.label,
            "command": [tool.command, *tool_args],
        }
