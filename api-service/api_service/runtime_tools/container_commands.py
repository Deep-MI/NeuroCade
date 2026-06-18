"""Container command and workspace bash helpers for runtime tools."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from api_service.runtime.execution import execute_runtime_request
from backend_common.case_storage import case_slug_from_id
from backend_common.settings import ROOT_DIR, get_settings
from neurocade_runtime_tools.container_specs import CORE_SPECS
from neurocade_runtime_tools.docker_command import RuntimeBind, build_docker_container_request, freesurfer_license_bind_env
from neurocade_runtime_tools.execution import (
    RuntimeArtifactIndexTarget,
    RuntimeContainerRunRequest,
    RuntimeExecutionPolicy,
    RuntimeExecutionRequest,
    RuntimeWorkspaceArtifactSyncTarget,
)

from .case_resolver import (
    CONTAINER_CASE_ROOT,
    resolve_case_mount_from_gui_state,
    resolve_host_path_via_existing_parents,
)
from .types import ToolTextContent, error_response, text_response

load_dotenv(ROOT_DIR / ".env")
settings = get_settings()

# Docker runtime execution for runtime tools.
HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR") or str(settings.fs_data_root)

LOCAL_DATA_ROOT = HOST_DATA_DIR
LOCAL_OUTPUT_ROOT = f"{HOST_DATA_DIR}/output"
CONTAINER_WORKSPACE_CASES_ROOT = "/cases"
CONTAINER_WORKSPACE_OUTPUT_ROOT = "/workspace"
FOCUS_LABEL_DEFAULT_SEGMENTATION = "aparc.DKTatlas+aseg.deep.mgz"
_SEGMENTATION_FILENAME_HINTS = (
    "aseg",
    "aparc",
    "seg",
    "mask",
    "cereb",
    "wmparc",
    "hypothal",
)
_VOLUME_FILE_SUFFIXES = (".mgz", ".mgh", ".nii", ".nii.gz")

def _docker_core_image(name: str) -> str:
    """Return the pinned Docker image for a core runtime container."""
    override = os.environ.get(f"NEUROCADE_{name.upper()}_IMAGE")
    if override:
        return override.removeprefix("docker://")
    if name == "bash_image":
        return os.environ.get("NEUROCADE_BASH_IMAGE", "neurocade-runtime-bash:local")
    spec = CORE_SPECS[name]
    if not spec.docker_uri:
        raise ValueError(f"Core container {name} does not define a Docker image")
    return spec.docker_uri.removeprefix("docker://")


def _docker_gpu_enabled() -> bool:
    """Return whether Docker runtime requests should ask for NVIDIA GPUs."""
    return os.environ.get("NEUROCADE_DOCKER_GPU", "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_workspace_bash_mount_path(host_path: str) -> tuple[str, tuple[str, ...]]:
    """Validate a host path and resolve it under the configured data root."""
    raw_path = str(host_path or "").strip()
    if not raw_path:
        raise ValueError("workspace_bash mount paths must not be empty")
    if not os.path.isabs(raw_path):
        raise ValueError("workspace_bash mount paths must be absolute host paths")
    raw_parts = Path(raw_path).parts
    if ".." in raw_parts:
        raise ValueError("workspace_bash mount paths must not traverse parent directories")

    resolved_host_path = _resolve_host_path_via_existing_parents(raw_path)
    if not resolved_host_path:
        raise ValueError("workspace_bash mount path is invalid")

    resolved_root = os.path.realpath(HOST_DATA_DIR)
    if os.path.commonpath([resolved_host_path, resolved_root]) != resolved_root:
        raise ValueError("workspace_bash mount path escapes the managed data root")
    relative_parts = Path(os.path.relpath(resolved_host_path, resolved_root)).parts
    return resolved_host_path, relative_parts


def _validate_workspace_case_bind_name(name: str) -> str:
    """Return a safe case mount name for /cases/<name>."""
    candidate = str(name or "").strip()
    if not candidate or candidate in {".", ".."} or "/" in candidate or "\\" in candidate:
        raise ValueError("workspace_bash case mount names must be path-safe")
    return candidate


def _workspace_case_binds_from_cases_dir(cases_dir: str) -> list[RuntimeBind]:
    """Return per-case binds declared by the prepared workspace cases manifest."""
    manifest_path = Path(cases_dir) / "cases.json"
    if not manifest_path.is_file():
        raise ValueError("workspace_bash requires a prepared cases.json bind manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("workspace_bash cases.json bind manifest is invalid JSON") from exc
    if not isinstance(manifest, list):
        raise ValueError("workspace_bash cases.json bind manifest must be a list")

    binds: list[RuntimeBind] = []
    for entry in manifest:
        if not isinstance(entry, dict):
            raise ValueError("workspace_bash cases.json entries must be objects")
        mount_name = _validate_workspace_case_bind_name(str(entry.get("mount_name") or ""))
        host_path = str(entry.get("host_path") or "").strip()
        if not host_path:
            raise ValueError("workspace_bash cases.json entries require host_path")
        case_dir = Path(host_path).resolve(strict=True)
        if not case_dir.is_dir():
            raise ValueError("workspace_bash case mount target must be a directory")
        case_dir_text = str(case_dir)
        resolved_root = os.path.realpath(HOST_DATA_DIR)
        if os.path.commonpath([case_dir_text, resolved_root]) != resolved_root:
            raise ValueError("workspace_bash case mount escapes the managed data root")
        relative_parts = Path(os.path.relpath(case_dir_text, resolved_root)).parts
        if (
            len(relative_parts) != 5
            or relative_parts[0] != "output"
            or relative_parts[1] != "workspaces"
            or relative_parts[3] != "cases"
            or relative_parts[4] != mount_name
        ):
            raise ValueError("workspace_bash case mounts must target canonical workspace case directories")
        binds.append(RuntimeBind(case_dir_text, f"{CONTAINER_WORKSPACE_CASES_ROOT}/{mount_name}", "ro"))
    if not binds:
        raise ValueError("workspace_bash requires at least one prepared case mount")
    return binds


def _validate_workspace_bash_cases_dir(cases_dir: str) -> tuple[str, str, list[RuntimeBind]]:
    """Return the host cases directory, analysis ID, and per-case binds for workspace bash."""
    resolved_host_path, relative_parts = _resolve_workspace_bash_mount_path(cases_dir)
    if len(relative_parts) != 3 or relative_parts[0] != ".workspace-inputs" or relative_parts[2] != "cases":
        raise ValueError(
            "workspace_bash requires cases_dir under <data-root>/.workspace-inputs/<analysis_id>/cases"
        )
    analysis_id = relative_parts[1].strip()
    if not analysis_id:
        raise ValueError("workspace_bash requires a non-empty cases_dir analysis_id")
    if not os.path.isdir(resolved_host_path):
        raise ValueError("workspace_bash requires an existing managed cases_dir")
    return resolved_host_path, analysis_id, _workspace_case_binds_from_cases_dir(resolved_host_path)


def _validate_workspace_bash_workspace_dir(workspace_dir: str) -> tuple[str, str]:
    """Return the host workspace output directory and analysis ID."""
    resolved_host_path, relative_parts = _resolve_workspace_bash_mount_path(workspace_dir)
    if (
        len(relative_parts) == 5
        and relative_parts[0] == "output"
        and relative_parts[1] == "workspaces"
        and relative_parts[3] == "workspace-analyses"
    ):
        analysis_id = relative_parts[4].strip()
    else:
        raise ValueError(
            "workspace_bash requires workspace_dir under <data-root>/output/workspaces/<workspace-id>/workspace-analyses/<analysis-id>"
        )
    if not analysis_id:
        raise ValueError("workspace_bash requires a non-empty workspace_dir analysis_id")
    if os.path.exists(resolved_host_path) and not os.path.isdir(resolved_host_path):
        raise ValueError("workspace_bash requires workspace_dir to be a directory path")
    return resolved_host_path, analysis_id


def _validate_workspace_case_bash_case_dir(case_dir: str) -> str:
    """Resolve and validate an existing workspace case directory."""
    resolved_host_path, relative_parts = _resolve_workspace_bash_mount_path(case_dir)
    if (
        len(relative_parts) != 5
        or relative_parts[0] != "output"
        or relative_parts[1] != "workspaces"
        or relative_parts[3] != "cases"
    ):
        raise ValueError("workspace case bash requires case_dir under <data-root>/output/workspaces/<workspace-slug>/cases/<case-slug>")
    if len(relative_parts) >= 4 and relative_parts[3] == "workspace-analyses":
        raise ValueError("workspace case bash case_dir must point to a case directory, not a workspace analysis")
    if not os.path.isdir(resolved_host_path):
        raise ValueError("workspace case bash requires an existing case_dir")
    return resolved_host_path


def _resolve_workspace_bash_mounts(cases_dir: str, workspace_dir: str) -> tuple[list[RuntimeBind], str]:
    """Resolve per-case and workspace output binds for a workspace command."""
    _host_cases_dir, cases_analysis_id, case_binds = _validate_workspace_bash_cases_dir(cases_dir)
    host_workspace_dir, workspace_analysis_id = _validate_workspace_bash_workspace_dir(workspace_dir)
    if cases_analysis_id != workspace_analysis_id:
        raise ValueError("workspace_bash requires cases_dir and workspace_dir for the same analysis_id")
    return [*case_binds, RuntimeBind(host_workspace_dir, CONTAINER_WORKSPACE_OUTPUT_ROOT, "rw")], host_workspace_dir


def _current_case_relative_output_path(gui_state: dict | None) -> str | None:
    """Build the output-root-relative active case path from immutable IDs."""
    state = gui_state or {}
    workspace_id = str(state.get("current_workspace_id") or state.get("workspace_id") or "").strip()
    case_id = str(state.get("current_case_id") or state.get("case_id") or "").strip()
    if not workspace_id or not case_id:
        return None
    if any(separator in workspace_id or separator in case_id for separator in ("/", "\\")):
        return None
    if workspace_id in {".", ".."} or case_id in {".", ".."}:
        return None
    return f"workspaces/{workspace_id}/cases/{case_slug_from_id(workspace_id, case_id)}"


def _looks_like_segmentation(filename: str) -> bool:
    """Return whether a filename appears to reference a segmentation volume."""
    lower = filename.lower()
    return any(token in lower for token in _SEGMENTATION_FILENAME_HINTS)


def _resolve_case_mount_local_dir(gui_state: dict | None) -> str | None:
    """Resolve the active case directory that should be mounted at /case."""
    resolved = resolve_case_mount_from_gui_state(
        gui_state,
        data_root=LOCAL_DATA_ROOT,
        output_root=LOCAL_OUTPUT_ROOT,
    )
    return str(resolved) if resolved is not None else None


def _docker_run_workspace_bash(
    bash_cmd: str,
    *,
    cases_dir: str,
    workspace_dir: str,
    image: str | None = None,
) -> RuntimeContainerRunRequest:
    """Build a Docker request for a workspace-scoped bash command."""
    binds, _host_workspace_dir = _resolve_workspace_bash_mounts(cases_dir, workspace_dir)
    license_bind_env = freesurfer_license_bind_env(root=ROOT_DIR, data_root=HOST_DATA_DIR)
    env = None
    if license_bind_env is not None:
        license_bind, env = license_bind_env
        binds.append(license_bind)
    return build_docker_container_request(
        image=image or _docker_core_image("bash_image"),
        binds=binds,
        env=env,
        disable_network=True,
        gpu=_docker_gpu_enabled(),
        command=["/bin/bash", "-lc", bash_cmd],
    )


def _docker_run_workspace_case_bash(
    bash_cmd: str,
    *,
    case_dir: str,
    image: str | None = None,
) -> RuntimeContainerRunRequest:
    """Build a Docker request for an internal workspace single-case command."""
    binds = [
        RuntimeBind(case_dir, CONTAINER_CASE_ROOT, "rw"),
    ]
    license_bind_env = freesurfer_license_bind_env(root=ROOT_DIR, data_root=HOST_DATA_DIR)
    env = None
    if license_bind_env is not None:
        license_bind, env = license_bind_env
        binds.append(license_bind)
    return build_docker_container_request(
        image=image or _docker_core_image("bash_image"),
        binds=binds,
        env=env,
        disable_network=True,
        gpu=_docker_gpu_enabled(),
        command=["/bin/bash", "-lc", bash_cmd],
    )


def _resolve_host_path_via_existing_parents(host_path: str) -> str | None:
    """Resolve a host path using existing parent directories."""
    return resolve_host_path_via_existing_parents(host_path)


_LEGACY_RUNTIME_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.+-])/(?:data|output)(?:/|(?=$)|(?=[\s'\";|&<>()]))")


def _reject_legacy_runtime_paths(command: str) -> None:
    """Reject container paths that are no longer mounted in workspace runtime tools."""
    match = _LEGACY_RUNTIME_PATH_RE.search(command)
    if match:
        raise ValueError("workspace runtime commands may use /case, /cases, and /workspace only; /data and /output are not mounted.")


def execute_workspace_bash(arguments: dict) -> RuntimeContainerRunRequest:
    """Build a workspace-scoped runtime bash command from tool arguments."""
    command = str(arguments.get("command", "") or "").strip()
    cases_dir = str(arguments.get("cases_dir", "") or "").strip()
    workspace_dir = str(arguments.get("workspace_dir", "") or "").strip()
    if not command:
        raise ValueError("workspace_bash requires a command")
    _reject_legacy_runtime_paths(command)
    _binds, host_workspace_dir = _resolve_workspace_bash_mounts(cases_dir, workspace_dir)
    os.makedirs(host_workspace_dir, exist_ok=True)
    return _docker_run_workspace_bash(command, cases_dir=cases_dir, workspace_dir=host_workspace_dir)


def execute_workspace_case_bash(arguments: dict) -> RuntimeContainerRunRequest:
    """Build a runtime bash command scoped to one workspace case."""
    command = str(arguments.get("command", "") or "").strip()
    case_dir = str(arguments.get("case_dir", "") or "").strip()
    if not command:
        raise ValueError("workspace case bash requires a command")
    _reject_legacy_runtime_paths(command)
    host_case_dir = _validate_workspace_case_bash_case_dir(case_dir)
    return _docker_run_workspace_case_bash(command, case_dir=host_case_dir)


RUNTIME_TASK_TIMEOUT = int(os.environ.get("RUNTIME_TASK_TIMEOUT", "3600"))


def run_synchronous_runtime_task(
    name: str,
    cmd: RuntimeContainerRunRequest,
    *,
    db=None,
    artifact_index_targets: list[RuntimeArtifactIndexTarget] | tuple[RuntimeArtifactIndexTarget, ...] = (),
    workspace_artifact_sync_targets: list[RuntimeWorkspaceArtifactSyncTarget] | tuple[RuntimeWorkspaceArtifactSyncTarget, ...] = (),
    queue_name: str | None = None,
    task_id: str | None = None,
) -> list[ToolTextContent]:
    """Execute a runtime-backed tool command and return text content."""
    try:
        runtime_policy = RuntimeExecutionPolicy(
            runtime="docker",
            network_disabled=True,
            gpu_enabled=cmd.gpu_enabled,
        )
        runner_url = (settings.runtime_runner_url or "").strip().rstrip("/")
        if not runner_url:
            raise RuntimeError("RUNTIME_RUNNER_URL is required for Docker runtime execution")
        result = execute_runtime_request(
            RuntimeExecutionRequest(
                argv=[f"docker:{name}"],
                timeout_s=RUNTIME_TASK_TIMEOUT,
                execution_mode="host-runtime-runner",
                queue_name=queue_name,
                task_id=task_id,
                runtime_policy=runtime_policy,
                runtime_runner_url=runner_url or None,
                runtime_runner_token=(settings.runtime_runner_token or None),
                container_run=cmd,
                artifact_index_targets=tuple(artifact_index_targets),
                workspace_artifact_sync_targets=tuple(workspace_artifact_sync_targets),
            ),
            db=db,
        )
        if result.returncode != 0:
            return error_response(
                f"executing {name} (Exit code {result.returncode}).\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return text_response(f"Successfully executed {name}.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    except TimeoutError:
        return error_response(f"{name} timed out after {RUNTIME_TASK_TIMEOUT}s.")
    except Exception as e:
        return error_response(f"An unexpected error occurred: {str(e)}")


# --- GUI Handlers ---
