"""Test host runtime runner behavior for NeuroCade."""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import host_runtime_runner  # noqa: E402
from neurocade_runtime_tools.execution import RuntimeExecutionResult  # noqa: E402


RUNTIME_POLICY = {"runtime": "apptainer", "network_disabled": True, "gpu_enabled": False}


def test_host_runtime_runner_executes_runtime_command(monkeypatch, tmp_path):
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.delenv("APPTAINER_BIN", raising=False)
    calls = []

    def fake_execute(request):
        calls.append(
            {
                "command": request.command,
                "cwd": request.cwd,
                "timeout": request.timeout_s,
                "mode": request.execution_mode,
                "rootless": request.require_rootless_apptainer,
                "policy": request.runtime_policy,
            }
        )
        return RuntimeExecutionResult(request=request, returncode=7, stdout="out", stderr="err")

    monkeypatch.setattr(host_runtime_runner, "execute_runtime_request", fake_execute)
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "command": ["singularity", "exec", "--net", "--network", "none", "image.sif", "echo", "hi"],
            "cwd": str(tmp_path),
            "timeout_s": 5,
            "runtime_policy": RUNTIME_POLICY,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"returncode": 7, "stdout": "out", "stderr": "err"}
    assert calls == [
        {
            "command": ["singularity", "exec", "--net", "--network", "none", "image.sif", "echo", "hi"],
            "cwd": tmp_path,
            "timeout": 5,
            "mode": "host-runtime-runner-adapter",
            "rootless": True,
            "policy": host_runtime_runner.RuntimeExecutionPolicy(network_disabled=True, gpu_enabled=False),
        }
    ]


def test_host_runtime_runner_rejects_non_runtime_command(monkeypatch):
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.delenv("APPTAINER_BIN", raising=False)
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={"command": ["rm", "-rf", "/tmp/example"]},
    )

    assert response.status_code == 422


def test_host_runtime_runner_rejects_path_to_allowed_binary_name(monkeypatch):
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={"command": ["/tmp/singularity", "--version"]},
    )

    assert response.status_code == 422


def test_host_runtime_runner_rejects_elevated_runtime_options(monkeypatch):
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "command": ["apptainer", "exec", "--net", "--network", "none", "--fakeroot", "image.sif", "echo"],
            "runtime_policy": RUNTIME_POLICY,
        },
    )

    assert response.status_code == 400
    assert "Refusing elevated Apptainer options" in response.json()["detail"]


def test_host_runtime_runner_allows_bind_under_configured_root(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    bound_dir = data_root / "case-a"
    bound_dir.mkdir()
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_ALLOWED_BIND_ROOTS", str(data_root))
    calls = []

    def fake_execute(request):
        calls.append(request.command)
        return RuntimeExecutionResult(request=request, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(host_runtime_runner, "execute_runtime_request", fake_execute)
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "command": [
                "apptainer",
                "exec",
                "--net",
                "--network",
                "none",
                "--bind",
                f"{bound_dir}:/case:rw",
                "image.sif",
                "echo",
            ],
            "runtime_policy": RUNTIME_POLICY,
        },
    )

    assert response.status_code == 200
    assert calls


def test_host_runtime_runner_rejects_bind_outside_configured_root(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_ALLOWED_BIND_ROOTS", str(data_root))
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "command": [
                "apptainer",
                "exec",
                "--net",
                "--network",
                "none",
                "--bind",
                f"{outside}:/case:rw",
                "image.sif",
                "echo",
            ],
            "runtime_policy": RUNTIME_POLICY,
        },
    )

    assert response.status_code == 400
    assert "outside allowed roots" in response.json()["detail"]


def test_host_runtime_runner_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    client = TestClient(host_runtime_runner.app)

    response = client.post("/run", json={"command": ["singularity", "--version"]})

    assert response.status_code == 401


def test_host_runtime_runner_rejects_run_when_token_missing(monkeypatch):
    monkeypatch.delenv("HOST_RUNTIME_RUNNER_TOKEN", raising=False)
    client = TestClient(host_runtime_runner.app)

    response = client.post("/run", json={"command": ["singularity", "--version"]})

    assert response.status_code == 503


def test_host_runtime_runner_uses_configured_apptainer_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("HOST_RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.setenv("APPTAINER_BIN", "/opt/apptainer/bin/apptainer")
    calls = []

    def fake_execute(request):
        calls.append(request.command)
        return RuntimeExecutionResult(request=request, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host_runtime_runner, "execute_runtime_request", fake_execute)
    client = TestClient(host_runtime_runner.app)

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "command": ["singularity", "exec", "--net", "--network", "none", "image.sif", "echo", "hi"],
            "cwd": str(tmp_path),
            "timeout_s": 5,
            "runtime_policy": RUNTIME_POLICY,
        },
    )

    assert response.status_code == 200
    assert calls == [["/opt/apptainer/bin/apptainer", "exec", "--net", "--network", "none", "image.sif", "echo", "hi"]]
