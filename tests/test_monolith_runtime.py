"""Runtime adapter and local-executor regression tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from neurocade_runtime_tools import apptainer_runtime
from neurocade_runtime_tools.execution import BridgeBind, RuntimeContainerRunRequest, RuntimeExecutionRequest, execute_runtime_request
from neurocade_runtime_tools.protocol import RuntimeImageSpec


def _apptainer_fixture(tmp_path: Path) -> tuple[RuntimeContainerRunRequest, Path]:
    (tmp_path / "case").mkdir()
    sif = tmp_path / "tool.sif"
    sif.write_bytes(b"sif")
    request = RuntimeContainerRunRequest(
        image=RuntimeImageSpec("example/tool:1"),
        command=["tool", "--version"],
        binds=[BridgeBind("case", "/case", "ro")],
        env={"LC_ALL": "C"},
        cwd="/case",
        network_disabled=True,
        gpu_enabled=True,
    )
    return request, sif


def test_apptainer_builds_rootless_verified_sif_argv(tmp_path: Path) -> None:
    request, sif = _apptainer_fixture(tmp_path)
    argv = apptainer_runtime.build_container_argv(request, data_root=tmp_path, prepared_image=sif)
    rendered = " ".join(argv)
    assert argv[:3] == ["apptainer", "--quiet", "exec"]
    assert "--cleanenv --no-home --containall" in rendered
    assert "--net --network none" in rendered and "--nv" in argv
    assert f"{tmp_path / 'case'}:/case:ro" in argv
    assert "--fakeroot" not in argv and "--writable" not in argv and "sudo" not in argv


def test_apptainer_enforces_network_and_isolation_policy(tmp_path: Path) -> None:
    request, sif = _apptainer_fixture(tmp_path)
    request.network_disabled = False
    request.gpu_enabled = False
    argv = apptainer_runtime.build_container_argv(request, data_root=tmp_path, prepared_image=sif)
    assert "--net" not in argv and "--network" not in argv
    request.isolated = True
    with pytest.raises(ValueError, match="cannot use binds"):
        apptainer_runtime.build_container_argv(request, data_root=tmp_path, prepared_image=sif)


def test_apptainer_rejects_privilege_tokens(tmp_path: Path) -> None:
    request, sif = _apptainer_fixture(tmp_path)
    request.command = ["--fakeroot", "true"]
    with pytest.raises(ValueError, match="privilege escalation"):
        apptainer_runtime.build_container_argv(request, data_root=tmp_path, prepared_image=sif)


def test_trusted_local_subprocess_still_runs_without_bridge() -> None:
    result = execute_runtime_request(
        RuntimeExecutionRequest(argv=[sys.executable, "-c", "print('local-ok')"], timeout_s=5)
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "local-ok"
