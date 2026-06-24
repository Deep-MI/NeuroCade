"""Provide API service fastsurfer tasks behavior for NeuroCade."""

from __future__ import annotations

import logging
import os

from api_service.jobs import job_manager
from api_service.runtime.constants import FASTSURFER_QUEUE
from api_service.file_utils import safe_write_json
from api_service.runtime.execution import RuntimeCompletionGuard, execute_runtime_request
from backend_common.case_storage import case_slug_from_id
from backend_common.db import SessionLocal
from backend_common.settings import get_settings
from neurocade_runtime_tools.container_request import (
    RuntimeBind,
    build_container_request,
    core_container_image,
    container_gpu_enabled,
    freesurfer_license_bind_env,
)
from neurocade_runtime_tools.execution import RuntimeArtifactIndexTarget, RuntimeContainerRunRequest, RuntimeExecutionPolicy, RuntimeExecutionRequest

logger = logging.getLogger(__name__)
settings = get_settings()

RUN_FASTSURFER_TASK = "api_service.fastsurfer.run_fastsurfer_task"

HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR") or str(settings.fs_data_root)


def _container_data_path(path: str) -> str:
    resolved = os.path.realpath(path)
    data_root = os.path.realpath(HOST_DATA_DIR)
    if os.path.commonpath([resolved, data_root]) != data_root:
        return path
    return "/data/" + os.path.relpath(resolved, data_root).lstrip("./")


def is_cuda_runtime_available() -> bool:
    """Return whether the host can execute NVIDIA CUDA workloads."""
    try:
        result = execute_runtime_request(
            RuntimeExecutionRequest(
                argv=["nvidia-smi"],
                timeout_s=15,
                execution_mode="local-subprocess",
            )
        )
        return result.returncode == 0
    except Exception:
        return False


def resolve_fastsurfer_device() -> str:
    """Choose the FastSurfer compute device from configuration and CUDA availability."""
    mode = os.environ.get("FASTSURFER_DEVICE_MODE", "auto").strip().lower()
    if mode in {"cuda", "gpu"}:
        return "cuda"
    if mode == "cpu":
        return "cpu"
    if mode == "auto":
        return "cuda" if is_cuda_runtime_available() else "cpu"
    return "cpu"


def _build_fastsurfer_request(
    fastsurfer_args: list[str],
    output_dir: str,
    *,
    enable_gpu: bool,
    license_bind_env: tuple[RuntimeBind, dict[str, str]] | None = None,
) -> RuntimeContainerRunRequest:
    """Build a backend-agnostic container request that runs FastSurfer."""
    binds = [
        RuntimeBind(HOST_DATA_DIR, "/data", "ro"),
        RuntimeBind(output_dir, "/output", "rw"),
    ]
    env = None
    if license_bind_env is not None:
        license_bind, env = license_bind_env
        binds.append(license_bind)
    return build_container_request(
        image=core_container_image("fastsurfer"),
        binds=binds,
        env=env,
        disable_network=True,
        gpu=enable_gpu,
        command=fastsurfer_args,
    )


@job_manager.task(RUN_FASTSURFER_TASK)
def run_fastsurfer_task(
    case_id: str,
    input_path: str,
    output_dir: str,
    workspace_id: str | None = None,
    user_id: str | None = None,
    case_title: str | None = None,
    output_case_dir_name: str | None = None,
    seg_only: bool = False,
    surf_only: bool = False,
    no_bias: bool = False,
    no_cereb: bool = False,
    no_asegdkt: bool = False,
    no_hypothal: bool = False,
    three_t: bool = False,
    threads: int = 4,
    vox_size: str = "min",
) -> dict:
    """Run FastSurfer for a case and persist task status, logs, and output metadata."""
    fastsurfer_device = resolve_fastsurfer_device()
    enable_gpu = fastsurfer_device == "cuda" and container_gpu_enabled()
    if output_case_dir_name:
        storage_case_name = str(output_case_dir_name).strip()
    elif workspace_id:
        storage_case_name = case_slug_from_id(workspace_id, case_id)
    else:
        raise ValueError("workspace_id or output_case_dir_name is required for FastSurfer output storage")
    if not storage_case_name or "/" in storage_case_name or "\\" in storage_case_name or storage_case_name in {".", ".."}:
        raise ValueError("output_case_dir_name must be a path-safe case directory name")
    runtime_input_path = _container_data_path(input_path)
    fastsurfer_args = ["/fastsurfer/run_fastsurfer.sh", "--t1", runtime_input_path, "--sd", "/output", "--sid", storage_case_name]
    freesurfer_license = freesurfer_license_bind_env(data_root=HOST_DATA_DIR)
    surface_pipeline_requested = surf_only or not seg_only

    if seg_only:
        fastsurfer_args.append("--seg_only")
    if surf_only:
        fastsurfer_args.append("--surf_only")
    if no_bias:
        fastsurfer_args.append("--no_biasfield")
    if no_cereb:
        fastsurfer_args.append("--no_cereb")
    if no_asegdkt:
        fastsurfer_args.append("--no_asegdkt")
    if no_hypothal:
        fastsurfer_args.append("--no_hypothal")
    if three_t:
        fastsurfer_args.append("--3T")
    if freesurfer_license is not None:
        license_bind, _license_env = freesurfer_license
        fastsurfer_args.extend(["--fs_license", license_bind.container_path])

    fastsurfer_args.extend(
        [
            "--allow_root",
            "--threads",
            str(threads),
            "--vox_size",
            vox_size,
            "--device",
            fastsurfer_device,
            "--viewagg_device",
            fastsurfer_device,
        ]
    )
    log_dir = os.path.join(output_dir, storage_case_name, "scripts")
    os.makedirs(log_dir, exist_ok=True)
    stdout_path = os.path.join(log_dir, "stdout.log")
    stderr_path = os.path.join(log_dir, "stderr.log")

    case_dir = os.path.join(output_dir, storage_case_name)
    os.makedirs(case_dir, exist_ok=True)
    status_file = os.path.join(case_dir, "status.json")

    def write_status(status: str, error: str | None = None) -> None:
        data = {"status": status, "case_id": case_id}
        if error:
            data["error"] = error
        try:
            safe_write_json(status_file, data)
        except Exception as exc:
            logger.warning("Failed to write status for case %s: %s", case_id, exc)

    write_status("running")

    if surface_pipeline_requested and freesurfer_license is None:
        error_message = (
            "A FreeSurfer license is required for FastSurfer surface reconstruction. "
            "Set FREESURFER_LICENSE or place license.txt in HOST_DATA_DIR."
        )
        write_status("error", error_message)
        return {"status": "failed", "error": error_message, "case_id": case_id}

    artifact_index_targets = (
        (
            RuntimeArtifactIndexTarget(
                user_id=user_id,
                workspace_id=workspace_id,
                case_id=case_id,
                case_title=case_title,
            ),
        )
        if user_id and workspace_id
        else ()
    )
    request: RuntimeExecutionRequest | None = None
    completion = RuntimeCompletionGuard(SessionLocal)

    try:
        cmd = _build_fastsurfer_request(
            fastsurfer_args,
            output_dir,
            enable_gpu=enable_gpu,
            license_bind_env=freesurfer_license,
        )
        request = RuntimeExecutionRequest(
            argv=[],
            timeout_s=None,
            execution_mode="container",
            synchronous=True,
            queue_name=FASTSURFER_QUEUE,
            user_id=user_id,
            workspace_id=workspace_id,
            case_id=case_id,
            output_root=output_dir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            capture_output=False,
            runtime_policy=RuntimeExecutionPolicy(
                network_disabled=True,
                gpu_enabled=enable_gpu,
            ),
            container_run=cmd,
            log_lines=[
                f"[worker] FASTSURFER_DEVICE_MODE={os.environ.get('FASTSURFER_DEVICE_MODE', 'auto')} -> resolved device={fastsurfer_device}",
                f"[worker] gpu_enabled={enable_gpu}",
                f"[worker] Command: {cmd}",
            ],
            artifact_index_targets=artifact_index_targets,
        )
        result = execute_runtime_request(request, run_completion_hooks=False)

        if result.returncode != 0:
            stderr_tail = ""
            try:
                with open(stderr_path, "r", encoding="utf-8") as stderr_read:
                    stderr_tail = "".join(stderr_read.readlines()[-40:]).strip()
            except Exception:
                pass

            error_message = f"FastSurfer exited with code {result.returncode}."
            if stderr_tail:
                error_message = f"{error_message}\n{stderr_tail}"
            write_status("error", error_message)
            completion.complete(request)
            return {
                "status": "failed",
                "error": error_message,
                "return_code": result.returncode,
                "case_id": case_id,
            }

        write_status("finished")
        completion.complete(request)
        return {
            "status": "completed",
            "output_path": f"{output_dir}/{storage_case_name}",
            "case_id": case_id,
            "device": fastsurfer_device,
        }
    except Exception as exc:
        write_status("error", str(exc))
        completion.complete(request)
        return {"status": "failed", "error": str(exc), "case_id": case_id}
