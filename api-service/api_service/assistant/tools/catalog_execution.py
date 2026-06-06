"""Installed-tool catalog execution for assistant tools.

This module turns assistant ``tool_call`` requests into concrete runtime
commands from the NeuroCade installed-tool catalog. It resolves the catalog
record, prepares workspace or case mounts, builds the container command through
the runtime tools package, and executes it either locally or through the host
runtime runner service.
"""

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
from backend_common.case_storage import workspace_storage_dir
from backend_common.db import AssistantScope
from neurocade_runtime_tools.apptainer_command import RuntimeBind
from neurocade_runtime_tools.containers import installed_tools_path
from neurocade_runtime_tools.execution import RuntimeArtifactIndexTarget, RuntimeExecutionPolicy, RuntimeExecutionRequest


class CatalogToolCallArgs(BaseModel):
    tool: str = Field(..., description="Installed tool name or alias returned by tool_search. The resolved command is executed.")
    tool_args: list[str] = Field(default_factory=list, description="Command-line arguments to pass to the tool.")
    records_jsonl: str | None = Field(None, description="Optional path to an installed tool index JSONL file.")


class AssistantCatalogExecutor:
    """Resolve and execute installed catalog tools for assistant requests."""

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
        """Resolve a catalog tool request, execute it, and return JSON output.

        ``tool_args`` may be supplied as a shell-like string or list. The
        rendered response includes public execution metadata plus bounded
        stdout/stderr tails from the runtime command.
        """
        raw_args = dict(arguments)
        if isinstance(raw_args.get("tool_args"), str):
            raw_args["tool_args"] = shlex.split(str(raw_args["tool_args"]))
        parsed = CatalogToolCallArgs.model_validate(raw_args)
        records_jsonl = self.catalog_records_path(parsed.model_dump())
        if not records_jsonl.exists():
            return f"Error: installed tool index not found at {records_jsonl}. Run `./scripts/containers.sh refresh-index`."
        try:
            from neurocade_runtime_tools.runtime_router import build_container_command, ensure_image_exists, resolve_tool

            row = resolve_tool(parsed.tool, records_jsonl=records_jsonl)
            ensure_image_exists(row)
            command = build_container_command(
                row,
                parsed.tool_args,
                project_root=self.root_dir,
                binds=binds or [],
            )
        except Exception as exc:
            return f"Error preparing tool execution: {exc}"

        public_execution = self.public_catalog_execution(row, parsed.tool, parsed.tool_args)
        if not isinstance(command, list) or not command:
            return f"Error: catalog entry did not produce an executable command for {parsed.tool}."
        timeout_s = int(os.environ.get("NEURO_CLI_TOOL_TIMEOUT", "300"))
        try:
            execute_kwargs: dict[str, Any] = {"timeout_s": timeout_s}
            if db is not None:
                execute_kwargs["db"] = db
            if artifact_index_targets:
                execute_kwargs["artifact_index_targets"] = artifact_index_targets
            completed = self.execute_runtime_command([str(part) for part in command], **execute_kwargs)
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

    def catalog_records_path(self, arguments: dict[str, Any]) -> Path:
        """Return the server-managed installed-tool catalog path."""
        configured = os.environ.get("NEUROCADE_INSTALLED_TOOLS_JSONL")
        if configured:
            return Path(str(configured)).expanduser()
        return installed_tools_path(self.root_dir)

    def execute_runtime_command(
        self,
        command: list[str],
        *,
        timeout_s: int,
        db=None,
        artifact_index_targets: tuple[RuntimeArtifactIndexTarget, ...] = (),
    ) -> dict[str, Any]:
        """Execute a prepared runtime command locally or through the host runner.

        When ``HOST_RUNTIME_RUNNER_URL`` is configured in settings, execution is
        delegated to that service. Otherwise the command runs as a subprocess
        with the repository root as its working directory.
        """
        runner_url = (self.settings.host_runtime_runner_url or "").strip().rstrip("/")
        result = execute_runtime_request(
            RuntimeExecutionRequest(
                argv=command,
                cwd=self.root_dir,
                timeout_s=timeout_s,
                execution_mode="host-runtime-runner" if runner_url else "local-subprocess",
                require_rootless_apptainer=True,
                runtime_policy=RuntimeExecutionPolicy(network_disabled=True, gpu_enabled=False),
                artifact_index_targets=artifact_index_targets,
                host_runner_url=runner_url or None,
                host_runner_token=(self.settings.host_runtime_runner_token or None),
            ),
            db=db,
        )
        return result.as_dict()

    @staticmethod
    def public_catalog_execution(row: dict[str, Any], tool: str, tool_args: list[str]) -> dict[str, Any]:
        """Return non-sensitive execution metadata for assistant/tool logs."""
        container_command = str(row.get("container_command") or row.get("name") or "").strip()
        return {
            "tool": row.get("name"),
            "requested_tool": tool,
            "toolbox": row.get("toolbox"),
            "runtime": {
                "app": row.get("app"),
                "version": row.get("runtime_version"),
                "build_date": row.get("build_date"),
            },
            "command": [container_command, *tool_args] if container_command else list(tool_args),
        }
