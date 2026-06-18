from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api-service"))
sys.path.insert(0, str(ROOT / "packages" / "neurocade-runtime-tools" / "src"))

from api_service import runtime_runner
from neurocade_runtime_tools.docker_catalog import generate_core_docker_catalog
from neurocade_runtime_tools.docker_command import RuntimeBind, build_docker_container_request


def test_docker_container_request_normalizes_image_and_binds(tmp_path: Path) -> None:
    request = build_docker_container_request(
        image="docker://vnmd/dcm2niix_v1.0.20240202:20260512",
        command=["dcm2niix", "--help"],
        binds=[RuntimeBind(tmp_path, "/input", "ro")],
        cwd="/input",
    )

    assert request.image == "vnmd/dcm2niix_v1.0.20240202:20260512"
    assert request.command == ["dcm2niix", "--help"]
    assert request.binds[0].container_path == "/input"
    assert request.binds[0].mode == "ro"
    assert request.cwd == "/input"
    assert request.network_disabled is True


def test_docker_container_request_rejects_ambiguous_docker_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid Docker image reference"):
        build_docker_container_request(image="docker://-bad", command=["true"])

    with pytest.raises(ValueError, match="Container bind path contains unsupported characters"):
        build_docker_container_request(
            image="neurocade-runtime-bash:local",
            command=["true"],
            binds=[RuntimeBind(tmp_path, "/case,bad", "ro")],
        )

    with pytest.raises(ValueError, match="Container working directory must be absolute"):
        build_docker_container_request(image="neurocade-runtime-bash:local", command=["true"], cwd="case")


def test_runtime_runner_maps_container_data_bind_to_host_path(monkeypatch, tmp_path: Path) -> None:
    host_data = tmp_path / "neurocade-data"
    host_case = host_data / "output" / "case-1"
    host_case.mkdir(parents=True)
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setenv("RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.setenv("HOST_DATA_DIR", "/data")
    monkeypatch.setenv("NEUROCADE_HOST_DATA_DIR", str(host_data))
    monkeypatch.setenv("RUNTIME_RUNNER_ALLOWED_BIND_ROOTS", "/data")
    monkeypatch.setattr(runtime_runner.subprocess, "run", fake_run)

    client = TestClient(runtime_runner.app)
    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "timeout_s": 30,
            "container_run": {
                "image": "neurocade-runtime-bash:local",
                "command": ["bash", "-lc", "echo ok"],
                "binds": [
                    {"host_path": "/data/output/case-1", "container_path": "/case", "mode": "rw"},
                ],
                "network_disabled": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["stdout"] == "ok"
    assert captured["command"][:4] == ["docker", "run", "--rm", "--network"]
    assert f"type=bind,src={host_case},dst=/case" in captured["command"]


def test_runtime_runner_rejects_bind_outside_allowed_root(monkeypatch, tmp_path: Path) -> None:
    host_data = tmp_path / "neurocade-data"
    host_data.mkdir()
    monkeypatch.setenv("RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.setenv("HOST_DATA_DIR", "/data")
    monkeypatch.setenv("NEUROCADE_HOST_DATA_DIR", str(host_data))
    monkeypatch.setenv("RUNTIME_RUNNER_ALLOWED_BIND_ROOTS", "/data")

    client = TestClient(runtime_runner.app)
    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "container_run": {
                "image": "neurocade-runtime-bash:local",
                "command": ["bash"],
                "binds": [
                    {"host_path": "/etc", "container_path": "/host-etc", "mode": "ro"},
                ],
            },
        },
    )

    assert response.status_code == 400
    assert "outside allowed roots" in response.text


def test_runtime_runner_rejects_invalid_structured_docker_fields(monkeypatch, tmp_path: Path) -> None:
    host_data = tmp_path / "neurocade-data"
    host_data.mkdir()
    monkeypatch.setenv("RUNTIME_RUNNER_TOKEN", "secret")
    monkeypatch.setenv("HOST_DATA_DIR", "/data")
    monkeypatch.setenv("NEUROCADE_HOST_DATA_DIR", str(host_data))
    monkeypatch.setenv("RUNTIME_RUNNER_ALLOWED_BIND_ROOTS", "/data")

    client = TestClient(runtime_runner.app)
    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "container_run": {
                "image": "docker://-bad",
                "command": ["bash"],
                "binds": [
                    {"host_path": "/data/output", "container_path": "/case", "mode": "ro"},
                ],
            },
        },
    )
    assert response.status_code == 422
    assert "Invalid Docker image reference" in response.text

    response = client.post(
        "/run",
        headers={"Authorization": "Bearer secret"},
        json={
            "container_run": {
                "image": "neurocade-runtime-bash:local",
                "command": ["bash"],
                "binds": [
                    {"host_path": "/data/output,ambiguous", "container_path": "/case", "mode": "ro"},
                ],
            },
        },
    )
    assert response.status_code == 422
    assert "Bind host path contains unsupported characters" in response.text


def test_generate_core_docker_catalog_writes_pinned_runtime_rows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEUROCADE_BASH_IMAGE", "neurocade-runtime-bash:local")
    containers_path, tools_path = generate_core_docker_catalog(tmp_path)

    containers = json.loads(containers_path.read_text(encoding="utf-8"))
    tool_rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines()]

    assert any(row["name"] == "fastsurfer" and row["docker_uri"] == "vnmd/fastsurfer_2.4.2:20260115" for row in containers["containers"])
    assert any(row["name"] == "dcm2niix" and row["docker_uri"] == "vnmd/dcm2niix_v1.0.20240202:20260512" for row in tool_rows)
    assert any(row["name"] == "bash" and row["docker_uri"] == "neurocade-runtime-bash:local" for row in tool_rows)
