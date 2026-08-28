"""Unit and protocol tests for the host-native runtime bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import main as main_module
from neurocade_runtime_tools import bridge as bridge_module
from neurocade_runtime_tools import execution as execution_module
from neurocade_runtime_tools.apptainer_runtime import NvidiaCapability
from neurocade_runtime_tools.bridge import BridgeRuntime
from neurocade_runtime_tools.bridge_client import BridgeClient, BridgeError
from neurocade_runtime_tools.bridge_server import BridgeHTTPServer
from neurocade_runtime_tools.docker_runtime import build_docker_argv
from neurocade_runtime_tools.execution import (
    BridgeBind,
    RuntimeContainerRunRequest,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
)
from neurocade_runtime_tools.images import _DockerPullProgress, _storage_preflight, download_verified_file, prepare_image
from neurocade_runtime_tools.protocol import PROTOCOL_VERSION, RuntimeImageSpec, relative_to_data_root
from requests import Session


def _payload(root: Path, *, run_id: str = "run-1") -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "container": {
            "image": RuntimeImageSpec("example/tool:1.0").to_dict(),
            "command": ["true"],
            "binds": [{"source_relative": "case", "container_path": "/case", "mode": "rw"}],
            "env": {"LC_ALL": "C"},
            "cwd": "/case",
            "network_disabled": True,
            "gpu_enabled": False,
            "isolated": False,
        },
        "timeout_s": 5,
        "workdir_relative": "case",
        "capture_output": True,
        "log_lines": [],
    }


def test_runtime_image_spec_rejects_unpinned_tag_and_bad_checksums() -> None:
    with pytest.raises(ValueError, match="tagged"):
        RuntimeImageSpec("example/tool")
    with pytest.raises(ValueError, match="OCI digest"):
        RuntimeImageSpec("example/tool:1", oci_digest="latest")
    with pytest.raises(ValueError, match="together"):
        RuntimeImageSpec("example/tool:1", sif_url="https://example.test/tool.sif")


def test_runtime_image_spec_uses_backend_compatible_digest_references() -> None:
    digest = f"sha256:{'a' * 64}"
    spec = RuntimeImageSpec("registry.example.test:5000/example/tool:1.0", oci_digest=digest)

    assert spec.docker_reference == f"registry.example.test:5000/example/tool:1.0@{digest}"
    assert spec.apptainer_reference == f"registry.example.test:5000/example/tool@{digest}"


def test_apptainer_pull_uses_digest_without_tag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    digest = f"sha256:{'b' * 64}"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):  # noqa: ANN202
        commands.append(command)
        Path(command[3]).write_bytes(b"sif")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("neurocade_runtime_tools.images.run_managed_command", fake_run)
    prepared = prepare_image(
        RuntimeImageSpec("deepmi/fastsurfer:1.0", oci_digest=digest),
        backend="apptainer",
        image_dir=tmp_path,
    )

    assert Path(prepared).is_file()
    assert commands == [[
        "apptainer",
        "pull",
        "--force",
        str(tmp_path / "deepmi_fastsurfer_1.0.sif.partial"),
        f"docker://deepmi/fastsurfer@{digest}",
    ]]


def test_verified_download_uses_valid_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "tool.sif"
    target.write_bytes(b"verified")
    checksum = hashlib.sha256(b"verified").hexdigest()
    monkeypatch.setattr("neurocade_runtime_tools.images.requests.get", lambda *_args, **_kwargs: pytest.fail("downloaded"))

    assert download_verified_file("https://example.test/tool.sif", target, expected_sha256=checksum) == target


def test_capture_buffer_never_exceeds_protocol_limit() -> None:
    capture = bridge_module._CaptureBuffer(limit=100)
    capture.append("a" * 1000)
    assert len(capture.value()) <= 100
    assert "truncated" in capture.value()


def test_docker_pull_progress_tracks_download_and_extraction() -> None:
    updates: list[dict] = []
    tracker = _DockerPullProgress("example/tool:1.0", updates.append)

    for line in (
        "aaaa: Pulling fs layer",
        "bbbb: Pulling fs layer",
        "\x1b[2Kaaaa: Downloading [========>] 5MiB/10MiB\r",
        "bbbb: Downloading [===>] 2 MiB / 10 MiB",
    ):
        tracker.feed(line)

    assert updates[-1]["phase"] == "downloading"
    assert updates[-1]["current_bytes"] == 7 * 1024**2
    assert updates[-1]["total_bytes"] == 20 * 1024**2
    assert updates[-1]["progress"] == 0.35
    assert updates[-1]["total_layers"] == 2

    tracker.feed("aaaa: Extracting [====>] 4MiB/20MiB")
    assert updates[-1]["phase"] == "extracting"
    assert updates[-1]["progress"] == 0.2

    tracker.feed("aaaa: Pull complete")
    assert updates[-1]["phase"] == "preparing"
    assert "progress" not in updates[-1]


def test_image_storage_preflight_warns_and_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "neurocade_runtime_tools.images._docker_reclaimable_summary",
        lambda: {"Images": "12GB (80%)", "Build Cache": "3GB"},
    )
    monkeypatch.setattr(
        "neurocade_runtime_tools.images.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=20 * 1024**3),
    )
    result = _storage_preflight()
    assert result["disk_free_bytes"] == 20 * 1024**3
    assert "20.0 GiB" in str(result["disk_warning"])
    assert result["reclaimable_storage"] == {"Images": "12GB (80%)", "Build Cache": "3GB"}

    monkeypatch.setattr(
        "neurocade_runtime_tools.images.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=4 * 1024**3),
    )
    with pytest.raises(RuntimeError, match="requires at least 5 GiB"):
        _storage_preflight()


def test_relative_path_conversion_rejects_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert relative_to_data_root(root / "case", root, label="case") == "case"
    with pytest.raises(ValueError, match="stay under"):
        relative_to_data_root(outside, root, label="case")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="stay under"):
        relative_to_data_root(root / "link" / "file", root, label="case")


def test_docker_builder_is_hardened_and_has_no_nested_privilege(tmp_path: Path) -> None:
    source = tmp_path / "case"
    source.mkdir()
    request = RuntimeContainerRunRequest(
        image=RuntimeImageSpec("example/tool:1"),
        command=["tool", "--version"],
        binds=[BridgeBind("case", "/case", "ro")],
        cwd="/case",
        network_disabled=True,
        gpu_enabled=True,
        run_id="abc",
    )
    argv = build_docker_argv(request, data_root=tmp_path)
    rendered = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network none" in rendered
    assert "--read-only" in argv and "--cap-drop ALL" in rendered
    assert "no-new-privileges" in argv and "--gpus all" in rendered
    assert "readonly" in rendered
    entrypoint_index = argv.index("--entrypoint")
    assert argv[entrypoint_index + 1] == "tool"
    assert argv[-2:] == ["example/tool:1", "--version"]
    assert "--privileged" not in argv and "/dev/fuse" not in rendered


def test_docker_builder_requires_explicit_command(tmp_path: Path) -> None:
    request = RuntimeContainerRunRequest(
        image=RuntimeImageSpec("example/tool:1"),
        command=[],
    )
    with pytest.raises(ValueError, match="explicit command"):
        build_docker_argv(request, data_root=tmp_path)


def test_bridge_duplicate_run_ids_and_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "case").mkdir(parents=True)
    runtime = BridgeRuntime(backend="docker", data_root=data_root, image_dir=tmp_path / "images")
    monkeypatch.setattr(bridge_module, "prepare_image", lambda *_args, **_kwargs: "example/tool:1")
    monkeypatch.setattr(
        bridge_module,
        "build_docker_argv",
        lambda *_args, **_kwargs: [sys.executable, "-c", "print('bridge-ok')"],
    )
    payload = _payload(data_root)
    run, created = runtime.start(payload)
    assert created is True
    duplicate, created = runtime.start(payload)
    assert duplicate is run and created is False
    conflicting = json.loads(json.dumps(payload))
    conflicting["container"]["command"] = ["false"]
    with pytest.raises(FileExistsError):
        runtime.start(conflicting)
    for _ in range(100):
        if run.public()["state"] == "completed":
            break
        threading.Event().wait(0.01)
    assert run.public()["stdout"].strip() == "bridge-ok"


def test_capability_resolution_does_not_pull_when_host_has_no_gpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    runtime = BridgeRuntime(backend="docker", data_root=data_root, image_dir=tmp_path / "images")
    runtime.global_gpu = NvidiaCapability(available=False, reason="No host GPU")
    monkeypatch.setattr(bridge_module, "prepare_image", lambda *_args, **_kwargs: pytest.fail("pulled image"))

    result = runtime.resolve_capability(RuntimeImageSpec("example/tool:1.0").to_dict())

    assert result == {
        "protocol_version": PROTOCOL_VERSION,
        "cpu": True,
        "cuda": False,
        "reason": "No host GPU",
    }


def _client_request(tmp_path: Path) -> RuntimeExecutionRequest:
    return RuntimeExecutionRequest(
        timeout_s=5,
        container_run=RuntimeContainerRunRequest(
            image=RuntimeImageSpec("example/tool:1.0"),
            command=["true"],
            isolated=True,
            run_id="client-run",
        ),
    )


def test_bridge_client_recovers_from_transient_poll_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = BridgeClient("http://bridge.test", "x" * 43, tmp_path, poll_interval_s=0)
    responses: list[dict | Exception] = [
        {},
        BridgeError("temporary disconnect"),
        {"state": "accepted"},
        {"state": "completed", "returncode": 0, "stdout": "ok"},
    ]

    def request(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(client, "_request", request)

    result = client.execute(_client_request(tmp_path))

    assert result.stdout == "ok"
    assert responses == []


def test_bridge_client_publishes_changed_progress(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = BridgeClient("http://bridge.test", "x" * 43, tmp_path, poll_interval_s=0)
    progress = {"kind": "image", "phase": "downloading", "progress": 0.5}
    responses = [
        {},
        {"state": "accepted", "progress": progress},
        {"state": "accepted", "progress": progress},
        {"state": "completed", "returncode": 0, "progress": {**progress, "progress": 1.0}},
    ]
    observed: list[dict] = []
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: responses.pop(0))
    token = execution_module.runtime_progress_observer.set(observed.append)
    try:
        client.execute(_client_request(tmp_path))
    finally:
        execution_module.runtime_progress_observer.reset(token)

    assert [item["progress"] for item in observed] == [0.5, 1.0]


def test_bridge_client_republishes_unchanged_progress_as_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = BridgeClient("http://bridge.test", "x" * 43, tmp_path, poll_interval_s=0)
    progress = {
        "kind": "image",
        "phase": "downloading",
        "progress": 0.5,
        "updated_at": 70.0,
    }
    responses = [
        {},
        {"state": "accepted", "progress": progress},
        {"state": "accepted", "progress": progress},
        {"state": "completed", "returncode": 0, "progress": {**progress, "progress": 1.0}},
    ]
    clock = iter((100.0, 106.0, 107.0))
    observed: list[dict] = []
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("neurocade_runtime_tools.bridge_client.time.time", lambda: next(clock))
    token = execution_module.runtime_progress_observer.set(observed.append)
    try:
        client.execute(_client_request(tmp_path))
    finally:
        execution_module.runtime_progress_observer.reset(token)

    assert [item["progress"] for item in observed] == [0.5, 0.5, 1.0]
    assert observed[1]["stalled_seconds"] == 36
    assert observed[1]["process_active"] is True


def test_bridge_client_cancels_nonterminal_run_after_protocol_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = BridgeClient("http://bridge.test", "x" * 43, tmp_path, poll_interval_s=0)
    calls: list[tuple[str, str]] = []

    def request(method: str, path: str, **_kwargs):  # noqa: ANN003, ANN202
        calls.append((method, path))
        if method == "GET":
            return {"state": "not-a-state"}
        return {}

    monkeypatch.setattr(client, "_request", request)

    with pytest.raises(BridgeError, match="invalid run state"):
        client.execute(_client_request(tmp_path))

    assert calls[-1] == ("DELETE", "/v1/runs/client-run")


def test_async_runtime_cancellation_invokes_registered_backend_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    canceled = threading.Event()

    def execute(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        observer = execution_module.cancellation_observer.get()
        assert observer is not None
        observer(canceled.set)
        started.set()
        canceled.wait(timeout=2)
        return RuntimeExecutionResult(request=request, returncode=0)

    monkeypatch.setattr(execution_module, "execute_runtime_request", execute)

    async def scenario() -> None:
        request = RuntimeExecutionRequest(argv=["true"])
        task = asyncio.create_task(execution_module.execute_runtime_request_async(request))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert canceled.is_set()


def test_async_runtime_progress_returns_to_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict] = []

    def execute(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        observer = execution_module.runtime_progress_observer.get()
        assert observer is not None
        observer({"kind": "image", "phase": "downloading", "progress": 0.25})
        return RuntimeExecutionResult(request=request, returncode=0)

    monkeypatch.setattr(execution_module, "execute_runtime_request", execute)

    async def scenario() -> None:
        async def observe(payload: dict) -> None:
            received.append(payload)

        await execution_module.execute_runtime_request_async(
            RuntimeExecutionRequest(argv=["true"]),
            progress_observer=observe,
        )
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert received == [{"kind": "image", "phase": "downloading", "progress": 0.25}]


def test_cancel_terminates_an_image_pull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "case").mkdir(parents=True)
    runtime = BridgeRuntime(backend="docker", data_root=data_root, image_dir=tmp_path / "images")

    def slow_prepare(_spec, *, process_observer, **_kwargs):  # noqa: ANN001, ANN202
        from neurocade_runtime_tools.execution import run_managed_command

        run_managed_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            check=True,
            process_observer=process_observer,
        )
        return "example/tool:1"

    monkeypatch.setattr(bridge_module, "prepare_image", slow_prepare)
    run, _created = runtime.start(_payload(data_root, run_id="cancel-pull"))
    for _ in range(100):
        if run.process is not None:
            break
        threading.Event().wait(0.01)
    assert run.process is not None
    runtime.cancel(run.run_id)
    for _ in range(100):
        if run.public()["state"] == "canceled" and run.process is None:
            break
        threading.Event().wait(0.01)
    assert run.public()["state"] == "canceled"
    assert run.process is None


def test_http_bridge_requires_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    runtime = BridgeRuntime(backend="docker", data_root=data_root, image_dir=tmp_path / "images")
    try:
        server = BridgeHTTPServer(("127.0.0.1", 0), runtime, "x" * 43)
    except PermissionError:
        pytest.skip("sandbox does not permit loopback listeners")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/health"
    session = Session()
    try:
        assert session.get(url, timeout=2).status_code == 401
        response = session.get(url, headers={"Authorization": f"Bearer {'x' * 43}"}, timeout=2)
        assert response.status_code == 200
        assert response.json()["protocol_version"] == PROTOCOL_VERSION
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_application_startup_fails_when_bridge_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.settings, "neurocade_runtime", "docker")
    monkeypatch.setattr(
        main_module.BridgeClient,
        "from_environment",
        classmethod(lambda _cls: (_ for _ in ()).throw(RuntimeError("bridge unavailable"))),
    )
    main_module.app.state.skip_host_startup_services = False

    async def start() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    with pytest.raises(RuntimeError, match="bridge unavailable"):
        asyncio.run(start())
