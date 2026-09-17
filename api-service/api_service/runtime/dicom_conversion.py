"""Shared DICOM conversion through the authenticated host runtime bridge."""
import os
from pathlib import Path

from fastapi import HTTPException
from neurocade_runtime_tools.container_request import DCM2NIIX_IMAGE, RuntimeBind, build_container_request
from neurocade_runtime_tools.execution import RuntimeExecutionRequest, execute_runtime_request

from api_service.runtime import settings
from api_service.runtime_tools.runtime_images import runtime_image_spec


def run_dcm2niix(input_dir: Path, output_dir: Path) -> None:
    command = ["dcm2niix", "-z", "y", "-b", "y", "-ba", "y", "-c", "", "-o", "/output", "-f", "%p_%s", "/input"]
    request = build_container_request(
        image=runtime_image_spec(os.environ.get("NEUROCADE_DCM2NIIX_IMAGE", DCM2NIIX_IMAGE)),
        binds=[RuntimeBind(input_dir, "/input", "ro"), RuntimeBind(output_dir, "/output", "rw")],
        disable_network=True, command=command,
    )
    try:
        result = execute_runtime_request(RuntimeExecutionRequest(
            cwd=output_dir, timeout_s=settings.dicom_conversion_timeout_seconds,
            execution_mode="container", output_root=output_dir, workdir_root=output_dir, container_run=request,
        ))
    except FileNotFoundError as exc:
        raise HTTPException(500, "The configured host runtime is not installed or not on PATH") from exc
    except TimeoutError as exc:
        raise HTTPException(504, "DICOM conversion timed out") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "dcm2niix failed"
        raise HTTPException(400, f"DICOM conversion failed: {stderr[-1000:]}")
