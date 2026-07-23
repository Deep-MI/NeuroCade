"""Assistant tools for executing commands inside the active case container.

This module exposes the case-scoped ``python_run`` and ``bash`` tools used by
the assistant runtime. Both tools resolve the active case through the persisted
workspace and case context, mount that case read-write at ``/case`` inside the
managed bash container, and execute through the configured container runtime.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException
from neurocade_runtime_tools.container_request import RuntimeBind, build_container_request, core_container_image
from neurocade_runtime_tools.execution import RuntimeArtifactIndexTarget, RuntimeContainerRunRequest
from pydantic import BaseModel, Field

from api_service.assistant.tools.catalog_execution import AssistantCatalogExecutor
from api_service.assistant.tools.definition import ToolDefinition
from api_service.assistant.tools.registration import CASE_ONLY, ScopedToolRegistration
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.runtime.execution import case_artifact_index_target
from api_service.runtime_tools.case_resolver import CONTAINER_CASE_ROOT, resolve_case_mount_from_db


class CasePythonRunArgs(BaseModel):
    script_path: str = Field(
        ...,
        description=(
            "Python script to run inside the active case container. Use /case/... "
            "or a path relative to /case, for example /case/scripts/qc.py."
        ),
    )
    args: list[str] = Field(default_factory=list, description="Command-line arguments to pass to the script.")


class CaseBashArgs(BaseModel):
    command: str = Field(
        ...,
        description=(
            "Bash command to run inside the managed bash container. The active case "
            "is mounted read-write at /case and the command starts in /case."
        ),
    )


CASE_TOOL_REGISTRATIONS: tuple[ScopedToolRegistration, ...] = (
    ScopedToolRegistration(
        name="python_run",
        description=(
            "Run an existing Python script inside the active case container. "
            "The active case is mounted read-write at /case."
        ),
        parameters=CasePythonRunArgs.model_json_schema(),
        handler_name="case_python_run_tool",
        scopes=CASE_ONLY,
        requires_managed_bash=True,
    ),
    ScopedToolRegistration(
        name="bash",
        description=(
            "Run a bash command inside the managed bash container. "
            "The active case is mounted read-write at /case and the command starts in /case."
        ),
        parameters=CaseBashArgs.model_json_schema(),
        handler_name="case_bash_tool",
        scopes=CASE_ONLY,
        requires_managed_bash=True,
    ),
)
class AssistantCaseTools:
    """Build and run assistant tools that operate on the active case mount."""

    def __init__(self, *, settings, root_dir: Path, command_executor: AssistantCatalogExecutor) -> None:
        """Store runtime settings, repository roots, and the command executor."""
        self.settings = settings
        self.root_dir = root_dir
        self.command_executor = command_executor

    def managed_bash_available(self) -> bool:
        """Return whether the managed bash container image is configured."""
        return bool(core_container_image("bash_image").strip())

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        """Return case container tool definitions for the current assistant state.

        Tools are only exposed when the managed bash image is available. The
        returned handlers execute their blocking container work in a worker
        thread so the assistant event loop remains responsive.
        """
        managed_bash_available = self.managed_bash_available()

        definitions: list[ToolDefinition] = []
        for registration in CASE_TOOL_REGISTRATIONS:
            if not registration.exposed_in(str(state.get("scope") or "")):
                continue
            if registration.requires_managed_bash and not managed_bash_available:
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

    def active_case_dir(self, state: dict[str, Any]) -> Path:
        """Resolve the host directory for the active case.

        The state must include a database session, authenticated request
        context, workspace id, and case id. Access checks are performed through
        the normal workspace and case helpers before resolving the mount path.
        """
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        case_id = state.get("case_id")
        if db is None or context is None or workspace_id is None or case_id is None:
            raise HTTPException(status_code=400, detail="Case container tools require an active case")
        case, _role = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace_id)
        workspace, _workspace_role = get_workspace_for_user(db, workspace_id, context.user.id)
        case_dir = resolve_case_mount_from_db(db, self.settings, case, workspace)
        if case_dir is None:
            raise HTTPException(status_code=404, detail="Active case directory was not found")
        resolved = Path(case_dir).expanduser().resolve()
        if not resolved.is_dir():
            raise HTTPException(status_code=404, detail=f"Active case directory does not exist: {resolved}")
        return resolved

    def case_runtime_completion_context(self, state: dict[str, Any]) -> tuple[Any | None, tuple[RuntimeArtifactIndexTarget, ...]]:
        """Return DB and case indexing hook target for the active case."""
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        case_id = state.get("case_id")
        if db is None or context is None or workspace_id is None or case_id is None:
            return None, ()
        case, _role = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace_id)
        return db, (case_artifact_index_target(case),)

    def case_python_run_tool(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        """Run an existing Python script from the active case inside the container.

        ``script_path`` may be a path under ``/case`` or a path relative to the
        case root. The path is normalized, checked for directory traversal, and
        required to point to an existing host-side file before execution.
        """
        parsed = CasePythonRunArgs.model_validate(arguments)
        case_dir = self.active_case_dir(state)
        script_relative = self.case_relative_path(parsed.script_path)
        script_host_path = (case_dir / script_relative).resolve(strict=False)
        if os.path.commonpath([str(script_host_path), str(case_dir)]) != str(case_dir):
            raise HTTPException(status_code=400, detail="script_path escapes the active case directory")
        if not script_host_path.is_file():
            return f"Error: Python script not found: /case/{script_relative}"
        db, artifact_index_targets = self.case_runtime_completion_context(state)
        command = [
            "bash",
            "-lc",
            "cd "
            + shlex.quote(CONTAINER_CASE_ROOT)
            + " && "
            + shlex.join(["python3.12", f"{CONTAINER_CASE_ROOT}/{script_relative}", *[str(arg) for arg in parsed.args]]),
        ]
        return self.execute_case_container_command(
            "python_run",
            case_dir,
            command,
            db=db,
            artifact_index_targets=artifact_index_targets,
        )

    def case_bash_tool(self, state: dict[str, Any], arguments: dict[str, Any]) -> str:
        """Run a bash command from ``/case`` inside the managed bash container."""
        parsed = CaseBashArgs.model_validate(arguments)
        command = parsed.command.strip()
        if not command:
            return "Error: command must not be empty."
        case_dir = self.active_case_dir(state)
        db, artifact_index_targets = self.case_runtime_completion_context(state)
        return self.execute_case_container_command(
            "bash",
            case_dir,
            ["bash", "-lc", f"cd {shlex.quote(CONTAINER_CASE_ROOT)} && {command}"],
            db=db,
            artifact_index_targets=artifact_index_targets,
        )

    def execute_case_container_command(
        self,
        tool_name: str,
        case_dir: Path,
        command: list[str],
        *,
        db=None,
        artifact_index_targets: tuple[RuntimeArtifactIndexTarget, ...] = (),
    ) -> str:
        """Execute a command in the managed case container and render JSON output.

        The active case is mounted read-write at ``/case``. Network access, home
        mounts, and the host current working directory mount are disabled before
        dispatching the command through the catalog executor.
        """
        try:
            runtime_command = self.build_case_runtime_command(case_dir, command)
        except Exception as exc:
            return f"Error preparing {tool_name}: {exc}"
        timeout_s = int(os.environ.get("PYTHON_TOOLS_TIMEOUT", os.environ.get("NEURO_CLI_TOOL_TIMEOUT", "300")))
        try:
            completed = self.command_executor.execute_runtime_command(
                runtime_command,
                timeout_s=timeout_s,
                db=db,
                artifact_index_targets=artifact_index_targets,
            )
        except Exception as exc:
            return f"Error executing {tool_name}: {exc}"
        payload = {
            "returncode": completed["returncode"],
            "stdout": completed["stdout"][-20000:],
            "stderr": completed["stderr"][-20000:],
            "execution_backend": completed["execution_backend"],
        }
        rendered = json.dumps(payload, indent=2)
        if completed["returncode"] != 0:
            return f"Error: {tool_name} exited with code {completed['returncode']}.\n{rendered}"
        return rendered

    def build_case_runtime_command(self, case_dir: Path, command: list[str]) -> RuntimeContainerRunRequest:
        """Build a case-scoped Docker runtime request."""
        binds = [RuntimeBind(case_dir, CONTAINER_CASE_ROOT, "rw")]
        return build_container_request(
            image=core_container_image("bash_image"),
            command=command,
            binds=binds,
            cwd=CONTAINER_CASE_ROOT,
            disable_network=True,
        )

    @staticmethod
    def case_relative_path(raw_path: str) -> str:
        """Normalize and validate a user-supplied path below ``/case``.

        Absolute paths are accepted only when rooted at ``/case``. Empty paths,
        null bytes, current-directory segments, and parent-directory segments
        are rejected so callers can safely join the result against the host case
        directory.
        """
        raw = str(raw_path or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="script_path must not be empty")
        if "\0" in raw:
            raise HTTPException(status_code=400, detail="script_path contains a null byte")
        if raw == CONTAINER_CASE_ROOT:
            raise HTTPException(status_code=400, detail="script_path must point to a file under /case")
        if raw.startswith(f"{CONTAINER_CASE_ROOT}/"):
            raw = raw.removeprefix(f"{CONTAINER_CASE_ROOT}/")
        elif raw.startswith("/"):
            raise HTTPException(status_code=400, detail="Only /case paths are allowed for python_run")

        path = PurePosixPath(raw)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise HTTPException(status_code=400, detail="script_path must not contain empty, current, or parent path segments")
        return path.as_posix()
