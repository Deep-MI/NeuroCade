"""Test shared runtime execution helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.cases import uploads as uploads_module  # noqa: E402
from neurocade_runtime_tools.execution import (  # noqa: E402
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    execute_runtime_request,
    run_managed_command,
)


def test_runtime_execution_captures_bounded_local_result(tmp_path):
    result = execute_runtime_request(
        RuntimeExecutionRequest(
            argv=[sys.executable, "-c", "print('ok')"],
            cwd=tmp_path,
            timeout_s=10,
            execution_mode="test-subprocess",
        )
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert result.execution_backend == "test-subprocess"


def test_runtime_execution_rejects_uncontained_log_path(tmp_path):
    with pytest.raises(ValueError, match="stdout log must stay under"):
        execute_runtime_request(
            RuntimeExecutionRequest(
                argv=[sys.executable, "-c", "print('nope')"],
                output_root=tmp_path / "allowed",
                stdout_path=tmp_path / "outside.log",
            )
        )


def test_managed_command_terminates_on_timeout() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_managed_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.01)


def test_managed_command_cleans_up_when_observer_interrupts() -> None:
    observed: list[subprocess.Popen[str]] = []

    def interrupt(process: subprocess.Popen[str] | None) -> None:
        if process is not None:
            observed.append(process)
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_managed_command([sys.executable, "-c", "import time; time.sleep(30)"], process_observer=interrupt)
    assert observed[0].poll() is not None


def test_dicom_conversion_uses_shared_runtime_execution(monkeypatch, tmp_path):
    input_dir = tmp_path / "dicom-input"
    output_dir = tmp_path / "dicom-output"
    input_dir.mkdir()
    output_dir.mkdir()
    captured: dict[str, RuntimeExecutionRequest] = {}

    monkeypatch.setattr(uploads_module.settings, "dicom_conversion_timeout_seconds", 17)

    def fake_execute(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        captured["request"] = request
        return RuntimeExecutionResult(request=request, returncode=0, stdout="converted", stderr="")

    monkeypatch.setattr(uploads_module, "execute_runtime_request", fake_execute)

    uploads_module._run_dcm2niix(input_dir, output_dir)

    request = captured["request"]
    assert request.cwd == output_dir
    assert request.output_root == output_dir
    assert request.workdir_root == output_dir
    assert request.timeout_s == 17
    # dcm2niix now runs in-process through the selected runtime backend.
    assert request.execution_mode == "container"
    assert request.container_run is not None
    assert request.container_run.command[:2] == ["dcm2niix", "-z"]
    assert request.container_run.network_disabled is True
    assert request.container_run.gpu_enabled is False
