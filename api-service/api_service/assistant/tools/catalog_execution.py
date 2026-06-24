"""Installed-tool catalog execution for assistant tools.

This module turns assistant ``tool_call`` requests into concrete runtime
commands from the NeuroCade installed-tool catalog. It resolves the catalog
record, prepares workspace or case mounts, builds the container command through
the runtime tools package, and executes it in-process via the selected runtime
backend (Apptainer or Docker).
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
from neurocade_runtime_tools.container_specs import CORE_SPECS
from neurocade_runtime_tools.container_request import RuntimeBind, build_container_request
from neurocade_runtime_tools.execution import RuntimeArtifactIndexTarget, RuntimeContainerRunRequest, RuntimeExecutionPolicy, RuntimeExecutionRequest


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
            return f"Error: installed tool index not found at {records_jsonl}. Run `./scripts/compose/images.sh` or restart the Compose stack."
        try:
            row = self.resolve_tool(parsed.tool, records_jsonl=records_jsonl)
            docker_image = self.docker_image_for_row(row)
            command = build_container_request(
                image=docker_image,
                command=[row["container_command"], *parsed.tool_args],
                binds=binds or [],
                cwd=self.container_cwd_for_binds(binds or []),
                disable_network=True,
                gpu=False,
            )
        except Exception as exc:
            return f"Error preparing tool execution: {exc}"

        public_execution = self.public_catalog_execution(row, parsed.tool, parsed.tool_args)
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

    def catalog_records_path(self, arguments: dict[str, Any]) -> Path:
        """Return the server-managed installed-tool catalog path."""
        configured = os.environ.get("NEUROCADE_INSTALLED_TOOLS_JSONL")
        if configured:
            return Path(str(configured)).expanduser()
        return self.settings.installed_tools_jsonl

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
    def docker_image_for_row(row: dict[str, Any]) -> str:
        """Return the Docker image for a catalog row, using core pinned defaults."""
        docker_uri = str(row.get("docker_uri") or "").strip()
        if docker_uri:
            return docker_uri
        name = str(row.get("name") or row.get("app") or "").strip()
        spec = CORE_SPECS.get(name)
        if spec and spec.docker_uri:
            return spec.docker_uri
        raise ValueError(
            f"Catalog row {name or '<unknown>'} does not define a Docker image. "
            "Docker runtime supports the pinned core catalog only."
        )

    @staticmethod
    def resolve_tool(tool: str, *, records_jsonl: Path) -> dict[str, Any]:
        """Resolve a tool name or unambiguous alias from the generated Docker catalog."""
        rows = [
            json.loads(line)
            for line in records_jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        lookup: dict[str, dict[str, Any]] = {}
        ambiguous_aliases: set[str] = set()
        protected_names = {str(row.get("name") or "") for row in rows}
        for row in rows:
            name = str(row.get("name") or "").strip()
            if name:
                lookup[name] = row
        for row in rows:
            for alias in row.get("aliases") or []:
                alias = str(alias or "").strip()
                if not alias or alias in protected_names:
                    continue
                existing = lookup.get(alias)
                if existing and existing.get("name") != row.get("name"):
                    ambiguous_aliases.add(alias)
                    continue
                lookup[alias] = row
        for alias in ambiguous_aliases:
            lookup.pop(alias, None)
        row = lookup.get(tool)
        if row is None:
            raise ValueError(f"Tool {tool!r} was not found in {records_jsonl}. Run tool_search first and pass an exact catalog name.")
        for key in ("docker_uri", "container_command"):
            if not row.get(key):
                raise ValueError(f"Tool {tool!r} is missing required field {key!r}.")
        return row

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
