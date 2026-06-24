"""Provide API service runtime service behavior for NeuroCade."""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from api_service.jobs import job_manager
from api_service.runtime.constants import FASTSURFER_QUEUE
from api_service.runtime.fastsurfer_tasks import RUN_FASTSURFER_TASK
from api_service.file_utils import safe_write_json
from api_service.runtime.execution import submit_runtime_request
from api_service.runtime.gui_state import GuiStateStore
from api_service.runtime.tool_dispatcher import RuntimeToolDispatcher, text_result
from api_service.runtime_tools import (
    execute_workspace_bash,
    execute_workspace_case_bash,
    run_synchronous_runtime_task,
)
from neurocade_runtime_tools.execution import RuntimeArtifactIndexTarget, RuntimeExecutionRequest, RuntimeWorkspaceArtifactSyncTarget
from backend_common.case_storage import case_slug_from_id, case_storage_dir
from backend_common.settings import ROOT_DIR, get_settings

settings = get_settings()

OUTPUT_DIR = settings.outputs_dir
LUT_PATH = ROOT_DIR / "config" / "FreeSurferColorLUT.txt"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _case_dir_for_id(case_id: str, workspace_id: str) -> Path | None:
    """Return the runtime case directory for a workspace/case id pair."""
    normalized_case_id = str(case_id or "").strip()
    if not normalized_case_id:
        return None
    if "/" in normalized_case_id or "\\" in normalized_case_id or normalized_case_id in {".", ".."}:
        return None
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        return None
    if "/" in normalized_workspace_id or "\\" in normalized_workspace_id or normalized_workspace_id in {".", ".."}:
        return None
    return case_storage_dir(settings, normalized_workspace_id, normalized_case_id)


class RuntimeService:
    def __init__(
        self,
        *,
        gui_state_store: GuiStateStore | None = None,
        tool_dispatcher: RuntimeToolDispatcher | None = None,
    ) -> None:
        self.gui_state_store = gui_state_store or GuiStateStore()
        self.tool_dispatcher = tool_dispatcher or RuntimeToolDispatcher(self.gui_state_store)

    def gui_state_for_key(self, state_key: str | None = None) -> dict[str, Any]:
        """Return the persisted GUI state for the requested state key."""
        return self.gui_state_store.state_for_key(state_key)

    async def fetch_tools(
        self,
        *,
        gui_state_key: str | None = None,
        gui_state_override: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Return runtime tools available for the current GUI state."""
        return self.tool_dispatcher.fetch_tools(
            gui_state_key=gui_state_key,
            gui_state_override=gui_state_override,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        gui_state_override: dict | None = None,
        *,
        gui_state_key: str | None = None,
    ) -> str:
        """Dispatch a named runtime tool with validated arguments."""
        return self.tool_dispatcher.call_tool(
            name,
            arguments,
            gui_state_override=gui_state_override,
            gui_state_key=gui_state_key,
        )

    async def fetch_gui_state(self, *, gui_state_key: str | None = None) -> dict[str, Any]:
        """Fetch persisted GUI state for the selected state key."""
        return self.gui_state_store.fetch(gui_state_key=gui_state_key)

    async def sync_gui_state(self, payload: dict, *, gui_state_key: str | None = None) -> dict[str, Any]:
        """Merge incoming GUI state into the selected stored state."""
        return self.gui_state_store.sync(payload, gui_state_key=gui_state_key)

    async def run_workspace_command(
        self,
        *,
        command: str,
        cases_dir: str,
        workspace_dir: str,
        db=None,
        workspace_artifact_sync_targets: list[RuntimeWorkspaceArtifactSyncTarget] | tuple[RuntimeWorkspaceArtifactSyncTarget, ...] = (),
        queue_name: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Execute a workspace-scoped shell command in the runtime container."""
        cmd = execute_workspace_bash(
            {
                "command": command,
                "cases_dir": cases_dir,
                "workspace_dir": workspace_dir,
            }
        )
        return text_result(
            run_synchronous_runtime_task(
                "workspace_bash",
                cmd,
                db=db,
                workspace_artifact_sync_targets=workspace_artifact_sync_targets,
                queue_name=queue_name,
                task_id=task_id,
            )
        )

    async def run_workspace_case_command(
        self,
        *,
        command: str,
        case_dir: str,
        db=None,
        artifact_index_targets: list[RuntimeArtifactIndexTarget] | tuple[RuntimeArtifactIndexTarget, ...] = (),
        queue_name: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Execute a case-scoped shell command in the runtime container."""
        cmd = execute_workspace_case_bash({"command": command, "case_dir": case_dir})
        return text_result(
            run_synchronous_runtime_task(
                "workspace_case_bash",
                cmd,
                db=db,
                artifact_index_targets=artifact_index_targets,
                queue_name=queue_name,
                task_id=task_id,
            )
        )

    async def start_run(self, payload: dict) -> dict[str, Any]:
        """Queue a FastSurfer run from the supplied case configuration."""
        case_id = str(payload.get("case_id") or "").strip()
        subject_name = str(payload.get("subject_name") or "").strip() or None
        input_path = str(payload.get("input_path") or "").strip() or None
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if not case_id:
            raise HTTPException(status_code=400, detail="case_id is required for runtime output storage.")
        if "/" in case_id or "\\" in case_id or case_id in {".", ".."}:
            raise HTTPException(status_code=400, detail="case_id must be a path-safe immutable ID.")
        if not workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id is required for runtime output storage.")
        if "/" in workspace_id or "\\" in workspace_id or workspace_id in {".", ".."}:
            raise HTTPException(status_code=400, detail="workspace_id must be a path-safe immutable ID.")
        try:
            output_case_dir_name = case_slug_from_id(workspace_id, case_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        case_dir = case_storage_dir(settings, workspace_id, case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        if subject_name:
            (case_dir / "subject.txt").write_text(subject_name, encoding="utf-8")

        resolved_input_path = input_path
        if not resolved_input_path:
            candidate_inputs: list[str] = []
            for pattern in ("*.nii.gz", "*.nii", "*.mgz"):
                candidate_inputs.extend(glob.glob(str(case_dir / pattern)))
            if candidate_inputs:
                resolved_input_path = sorted(candidate_inputs)[0]
        if not resolved_input_path:
            raise HTTPException(status_code=400, detail="No input file provided and no existing case-local input found.")

        user_id = str(payload.get("user_id") or "").strip() or None
        case_title = subject_name or case_id
        task_kwargs = {
            "case_id": case_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "case_title": case_title,
            "output_case_dir_name": output_case_dir_name,
            "input_path": resolved_input_path,
            "output_dir": str(case_dir.parent),
            "seg_only": _as_bool(payload.get("seg_only")),
            "surf_only": _as_bool(payload.get("surf_only")),
            "no_bias": _as_bool(payload.get("no_bias")),
            "no_cereb": _as_bool(payload.get("no_cereb")),
            "no_asegdkt": _as_bool(payload.get("no_asegdkt")),
            "no_hypothal": _as_bool(payload.get("no_hypothal")),
            "three_t": _as_bool(payload.get("three_t")),
            "threads": int(payload.get("threads") or 1),
            "vox_size": str(payload.get("vox_size") or "min"),
        }
        submission = submit_runtime_request(
            RUN_FASTSURFER_TASK,
            RuntimeExecutionRequest(
                argv=[RUN_FASTSURFER_TASK],
                execution_mode="job-submit",
                synchronous=False,
                queue_name=FASTSURFER_QUEUE,
                user_id=user_id,
                workspace_id=workspace_id,
                case_id=case_id,
                output_root=case_dir.parent,
                artifact_index_targets=(
                    RuntimeArtifactIndexTarget(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        case_id=case_id,
                        case_title=case_title,
                    ),
                )
                if user_id
                else (),
            ),
            kwargs=task_kwargs,
        )
        task_id = str(submission.submitted_task_id or submission.request.task_id or "")
        safe_write_json(
            str(case_dir / "status.json"),
            {
                "status": "queued",
                "case_id": case_id,
                "task_id": task_id,
                "subject_name": subject_name or case_id,
            },
        )
        return {"case_id": case_id, "task_id": task_id, "status": "queued"}

    async def fetch_status(self, case_id: str, workspace_id: str) -> dict[str, Any]:
        """Read the saved processing status for a case."""
        case_dir = _case_dir_for_id(case_id, workspace_id)
        status_file = case_dir / "status.json" if case_dir else None
        if status_file and status_file.exists():
            try:
                return json.loads(status_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"status": "unknown"}
        return {"status": "unknown"}

    async def fetch_task_status(self, task_id: str) -> dict[str, Any]:
        """Return background job readiness, status, and result when available."""
        return job_manager.status(task_id)

    async def fetch_queue_status(self) -> dict[str, int]:
        """Return active (running) and queued background job counts."""
        return job_manager.queue_status()

    async def cancel(self, case_id: str, workspace_id: str) -> None:
        """Cancel a queued or running case job and mark it canceled."""
        case_dir = _case_dir_for_id(case_id, workspace_id)
        status_file = case_dir / "status.json" if case_dir else None
        if status_file is None or not status_file.exists():
            raise HTTPException(status_code=404, detail="Case not found")
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            task_id = data.get("task_id")
            if task_id:
                job_manager.cancel(task_id)
            data["status"] = "canceled"
            safe_write_json(str(status_file), data)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def fetch_logs(self, case_id: str, workspace_id: str) -> dict[str, str]:
        """Return filtered stdout and stderr logs for a case."""
        case_dir = _case_dir_for_id(case_id, workspace_id)
        missing_dir = OUTPUT_DIR / "workspaces" / "_missing" / "cases" / case_id
        stdout_path = (case_dir / "scripts" / "stdout.log") if case_dir else missing_dir / "scripts" / "stdout.log"
        stderr_path = (case_dir / "scripts" / "stderr.log") if case_dir else missing_dir / "scripts" / "stderr.log"

        def read_safe(path: Path) -> list[str]:
            if not path.exists():
                return []
            try:
                raw = path.read_bytes().decode("utf-8", errors="replace")
                return [line + "\n" for line in raw.split("\n") if line]
            except OSError:
                return []

        combined = read_safe(stdout_path)
        stderr_lines = read_safe(stderr_path)
        if stderr_lines:
            combined += ["--- STDERR ---\n", *stderr_lines]

        filtered = [
            line
            for line in combined
            if not ("WARNING: Found" in line and "files in subject directory" in line)
            and "Potentially Overwriting:" not in line
        ]
        processed: list[str] = []
        for line in filtered:
            if "\r" not in line:
                processed.append(line)
                continue
            for segment in reversed(line.split("\r")):
                stripped = segment.strip()
                if stripped:
                    processed.append(stripped + "\n")
                    break
        return {"logs": "".join(processed[-1000:])}


runtime_service = RuntimeService()
