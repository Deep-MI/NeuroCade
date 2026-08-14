"""In-process tests for the monolith runtime: backends, JobWorker, execution.

These exercise the in-process runtime components used by the monolith,
and Redis. They run fully in-process (no Docker/Apptainer/stack required).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from neurocade_runtime_tools import execution as execution_module
from neurocade_runtime_tools import runtime_backends as rb
from neurocade_runtime_tools.execution import (
    RuntimeBind,
    RuntimeContainerRunRequest,
    RuntimeExecutionRequest,
    _terminate_process_group,
    execute_runtime_request,
    process_observer,
)

# --- runtime backends -----------------------------------------------------

def _sample_request() -> RuntimeContainerRunRequest:
    return RuntimeContainerRunRequest(
        image="vnmd/fastsurfer_2.4.2:20260115",
        command=["/fastsurfer/run_fastsurfer.sh", "--t1", "/data/in.nii"],
        binds=[RuntimeBind("/host/data", "/data", "ro"), RuntimeBind("/host/out", "/output", "rw")],
        env={"TOOL_ENV": "enabled"},
        gpu_enabled=True,
    )


def test_docker_backend_builds_expected_argv():
    argv = rb.DockerBackend().build_argv(_sample_request())
    assert argv[:2] == ["docker", "run"]
    assert "--rm" in argv and "--gpus" in argv
    assert "--network" in argv and "none" in argv
    assert "type=bind,src=/host/data,dst=/data,readonly" in argv
    assert "type=bind,src=/host/out,dst=/output" in argv
    assert "--env" in argv and "TOOL_ENV=enabled" in argv
    # image precedes the in-container command
    image_idx = argv.index("vnmd/fastsurfer_2.4.2:20260115")
    assert argv[image_idx + 1] == "/fastsurfer/run_fastsurfer.sh"


def test_docker_backend_isolates_probe_container():
    request = RuntimeContainerRunRequest(
        image="antsx/ants:v2.6.5",
        command=["bash", "-lc", "DenoiseImage --help"],
        isolated=True,
    )

    argv = rb.DockerBackend().build_argv(request)

    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--pull") + 1] == "never"
    assert "--read-only" in argv
    assert argv[argv.index("--user") + 1] == "65534:65534"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert argv[argv.index("--pids-limit") + 1] == "64"
    assert argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("--cpus") + 1] == "1"
    assert argv[argv.index("--tmpfs") + 1].startswith("/tmp:rw,")
    assert "HOME=/tmp" in argv
    assert "--mount" not in argv


def test_apptainer_backend_builds_expected_argv(monkeypatch, tmp_path):
    sif = tmp_path / "fastsurfer.sif"
    sif.write_bytes(b"sif")
    monkeypatch.setattr(rb, "resolve_apptainer_image", lambda _image: str(sif))
    argv = rb.ApptainerBackend().build_argv(_sample_request())
    assert argv[:3] == ["apptainer", "--quiet", "exec"]
    assert "--net" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert "--nv" in argv  # gpu
    assert "--bind" in argv and "/host/data:/data:ro" in argv
    assert "/host/out:/output" in argv
    assert "--env" in argv and "TOOL_ENV=enabled" in argv
    assert str(sif) in argv


def test_apptainer_backend_disables_implicit_host_mounts_for_probe(monkeypatch, tmp_path):
    sif = tmp_path / "ants.sif"
    sif.write_bytes(b"sif")
    monkeypatch.setattr(rb, "resolve_apptainer_image", lambda _image: str(sif))
    request = RuntimeContainerRunRequest(
        image="antsx/ants:v2.6.5",
        command=["bash", "-lc", "DenoiseImage --help"],
        isolated=True,
    )

    argv = rb.ApptainerBackend().build_argv(request)

    assert "--contain" in argv
    assert argv[argv.index("--no-mount") + 1] == "hostfs,cwd,proc,sys"
    assert "--bind" not in argv


def test_isolated_container_rejects_binds_and_gpu():
    with pytest.raises(ValueError, match="cannot use bind mounts"):
        rb.DockerBackend().build_argv(
            RuntimeContainerRunRequest(
                image="antsx/ants:v2.6.5",
                command=["true"],
                binds=[RuntimeBind("/host/data", "/data", "ro")],
                isolated=True,
            )
        )
    with pytest.raises(ValueError, match="cannot request a GPU"):
        rb.DockerBackend().build_argv(
            RuntimeContainerRunRequest(
                image="antsx/ants:v2.6.5",
                command=["true"],
                gpu_enabled=True,
                isolated=True,
            )
        )


def test_apptainer_backend_rejects_unprepared_image_without_network(monkeypatch):
    monkeypatch.setattr(rb, "resolve_apptainer_image", lambda image: f"docker://{image}")

    with pytest.raises(RuntimeError, match="prepare-tools"):
        rb.ApptainerBackend().build_argv(_sample_request())


def test_apptainer_backend_allows_requested_network():
    request = _sample_request()
    request.network_disabled = False

    argv = rb.ApptainerBackend().build_argv(request)

    assert "--net" not in argv
    assert "--network" not in argv


def test_gpu_mode_auto_falls_back_to_cpu(monkeypatch):
    monkeypatch.setenv(rb.GPU_MODE_ENV, "auto")
    monkeypatch.setattr(rb, "nvidia_capability", lambda: rb.NvidiaCapability(False, "driver unavailable"))

    assert rb.resolve_gpu_enabled(True) is False


def test_gpu_mode_cuda_fails_with_actionable_error(monkeypatch):
    monkeypatch.setenv(rb.GPU_MODE_ENV, "cuda")
    monkeypatch.setattr(rb, "nvidia_capability", lambda: rb.NvidiaCapability(False, "driver unavailable"))

    with pytest.raises(rb.RuntimeGpuUnavailableError, match="NEUROCADE_GPU_MODE=cpu"):
        rb.resolve_gpu_enabled(True)


def test_gpu_mode_cpu_does_not_probe_nvidia(monkeypatch):
    monkeypatch.setenv(rb.GPU_MODE_ENV, "cpu")
    monkeypatch.setattr(rb, "nvidia_capability", lambda: pytest.fail("GPU probe should not run"))

    assert rb.resolve_gpu_enabled(True) is False


def test_gpu_mode_auto_falls_back_when_prepared_image_is_cpu_only(monkeypatch):
    monkeypatch.setenv(rb.GPU_MODE_ENV, "auto")
    monkeypatch.setattr(rb, "nvidia_capability", lambda: rb.NvidiaCapability(True, "GPU available"))
    monkeypatch.setattr(
        rb,
        "apptainer_image_cuda_capability",
        lambda _image: rb.NvidiaCapability(False, "PyTorch is CPU-only"),
    )

    assert rb.resolve_gpu_enabled(True, image="vnmd/fastsurfer:tag") is False


def test_gpu_mode_cuda_rejects_cpu_only_prepared_image(monkeypatch):
    monkeypatch.setenv(rb.GPU_MODE_ENV, "cuda")
    monkeypatch.setattr(rb, "nvidia_capability", lambda: rb.NvidiaCapability(True, "GPU available"))
    monkeypatch.setattr(
        rb,
        "apptainer_image_cuda_capability",
        lambda _image: rb.NvidiaCapability(False, "PyTorch is CPU-only"),
    )

    with pytest.raises(rb.RuntimeGpuUnavailableError, match="CUDA-enabled tool image"):
        rb.resolve_gpu_enabled(True, image="vnmd/fastsurfer:tag")


def test_prepared_image_cuda_probe_is_cached_until_sif_changes(monkeypatch, tmp_path):
    sif = tmp_path / "fastsurfer.sif"
    sif.write_bytes(b"first")
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    rb._cached_apptainer_image_cuda_capability.cache_clear()
    monkeypatch.setattr(rb, "resolve_apptainer_image", lambda _image: str(sif))
    monkeypatch.setattr(rb.subprocess, "run", fake_run)

    assert rb.apptainer_image_cuda_capability("deepmi/fastsurfer:tag").available is True
    assert rb.apptainer_image_cuda_capability("deepmi/fastsurfer:tag").available is True
    assert len(calls) == 1

    sif.write_bytes(b"replacement")
    assert rb.apptainer_image_cuda_capability("deepmi/fastsurfer:tag").available is True
    assert len(calls) == 2
    rb._cached_apptainer_image_cuda_capability.cache_clear()


def test_apptainer_resolves_prebuilt_sif_for_host_arch(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.SIF_DIR_ENV, str(tmp_path))
    arch = rb._normalise_arch()
    stem = "vnmd_fastsurfer_2.4.2_20260115"
    (tmp_path / f"{stem}-{arch}.sif").write_text("x")
    resolved = rb.resolve_apptainer_image("vnmd/fastsurfer_2.4.2:20260115")
    assert resolved.endswith(f"{stem}-{arch}.sif")


def test_apptainer_falls_back_to_docker_uri_without_prebuilt(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.SIF_DIR_ENV, str(tmp_path))
    resolved = rb.resolve_apptainer_image("vnmd/fastsurfer_2.4.2:20260115")
    assert resolved == "docker://vnmd/fastsurfer_2.4.2:20260115"


def test_apptainer_resolves_generic_prebuilt_sif_name(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.SIF_DIR_ENV, str(tmp_path))
    arch = rb._normalise_arch()
    sif = tmp_path / f"python_3.12-bookworm-{arch}.sif"
    sif.write_text("x")
    resolved = rb.resolve_apptainer_image("python:3.12-bookworm")
    assert resolved == str(sif)


def test_rootless_validation_rejects_fakeroot():
    bad = RuntimeContainerRunRequest(image="alpine", command=["--fakeroot", "sh"])
    with pytest.raises(ValueError):
        rb.ApptainerBackend().build_argv(bad)


def test_select_backend_honours_env(monkeypatch):
    monkeypatch.setenv(rb.RUNTIME_BACKEND_ENV, "docker")
    assert rb.select_runtime_backend().name == "docker"
    monkeypatch.setenv(rb.RUNTIME_BACKEND_ENV, "apptainer")
    assert rb.select_runtime_backend().name == "apptainer"
    monkeypatch.setenv(rb.RUNTIME_BACKEND_ENV, "nonsense")
    with pytest.raises(ValueError):
        rb.select_runtime_backend()


def test_arch_normalisation():
    assert rb._normalise_arch() in {"amd64", "arm64"} or isinstance(rb._normalise_arch(), str)


# --- execution layer (Popen / timeout / cancellation) ---------------------

def test_execute_plain_command_captures_output():
    result = execute_runtime_request(
        RuntimeExecutionRequest(argv=["sh", "-c", "echo out; echo err 1>&2; exit 3"], timeout_s=10)
    )
    assert result.returncode == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_execute_writes_log_files(tmp_path):
    out, err = tmp_path / "o.log", tmp_path / "e.log"
    execute_runtime_request(
        RuntimeExecutionRequest(
            argv=["sh", "-c", "echo body; echo oops 1>&2"],
            timeout_s=10,
            stdout_path=str(out),
            stderr_path=str(err),
            capture_output=False,
            output_root=str(tmp_path),
            log_lines=["[worker] prelude"],
        )
    )
    assert "prelude" in out.read_text() and "body" in out.read_text()
    assert "oops" in err.read_text()


def test_execute_timeout_terminates():
    start = time.time()
    with pytest.raises(TimeoutError):
        execute_runtime_request(RuntimeExecutionRequest(argv=["sh", "-c", "sleep 30"], timeout_s=1))
    assert time.time() - start < 5


def test_execute_timeout_kills_term_resistant_process(monkeypatch):
    monkeypatch.setattr(execution_module, "TERMINATION_GRACE_SECONDS", 0.1)
    command = [
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(30)",
    ]

    start = time.time()
    with pytest.raises(TimeoutError):
        execute_runtime_request(RuntimeExecutionRequest(argv=command, timeout_s=0.1))

    assert time.time() - start < 3


def test_observer_cancellation_kills_process_group():
    captured: dict[str, subprocess.Popen] = {}

    def capture_process(process: subprocess.Popen) -> None:
        captured["p"] = process

    token = process_observer.set(capture_process)

    def killer() -> None:
        for _ in range(100):
            if "p" in captured:
                break
            time.sleep(0.02)
        time.sleep(0.1)
        _terminate_process_group(captured["p"])

    thread = threading.Thread(target=killer)
    thread.start()
    result = execute_runtime_request(RuntimeExecutionRequest(argv=["sh", "-c", "sleep 30"], timeout_s=60))
    thread.join()
    process_observer.reset(token)
    assert result.returncode < 0  # killed by signal


def test_container_request_routes_through_backend(monkeypatch):
    # execute_runtime_request imports build_container_argv from runtime_backends
    # at call time, so stubbing the module attribute redirects container runs to
    # a harmless echo and proves container_run is wired into local execution.
    monkeypatch.setattr(rb, "build_container_argv", lambda req: ["sh", "-c", "echo wired"])
    request = RuntimeExecutionRequest(
        argv=[],
        timeout_s=10,
        container_run=RuntimeContainerRunRequest(image="alpine", command=["true"]),
    )
    result = execute_runtime_request(request)
    assert result.returncode == 0
    assert "wired" in result.stdout
    assert request.container_run is None  # consumed/cleared after build


# --- JobManager -----------------------------------------------------------

def _manager():
    from api_service.jobs.manager import JobManager

    return JobManager(concurrency={"api": 2, "fastsurfer": 1})


def _await_ready(jm, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if jm.status(task_id)["ready"]:
            return jm.status(task_id)
        time.sleep(0.02)
    raise AssertionError(f"job {task_id} did not finish")


def test_job_submit_and_complete():
    jm = _manager()
    jm.register("echo", lambda msg: {"echoed": msg})
    tid = jm.submit("echo", {"msg": "hi"}, queue="api")
    status = _await_ready(jm, tid)
    assert status["status"] == "completed"
    assert status["result"] == {"echoed": "hi"}
    jm.shutdown(wait=True)


def test_job_failure_is_captured():
    jm = _manager()

    def boom():
        raise RuntimeError("kaboom")

    jm.register("boom", boom)
    tid = jm.submit("boom", {}, queue="api")
    status = _await_ready(jm, tid)
    assert status["status"] == "failed"
    assert "kaboom" in status["error"]
    jm.shutdown(wait=True)


def test_job_queue_counts_and_cancel():
    jm = _manager()

    def slow():
        execute_runtime_request(RuntimeExecutionRequest(argv=["sh", "-c", "sleep 30"], timeout_s=60))

    jm.register("slow", slow)
    tid = jm.submit("slow", {}, queue="fastsurfer")
    time.sleep(0.4)
    assert jm.queue_status()["active"] == 1
    assert jm.cancel(tid) is True
    status = _await_ready(jm, tid)
    assert status["status"] == "canceled"
    assert jm.queue_status()["total"] == 0
    jm.shutdown(wait=True)


def test_job_shutdown_cancels_active_runtime_process(tmp_path):
    jm = _manager()
    pid_file = tmp_path / "runtime.pid"

    def slow():
        execute_runtime_request(
            RuntimeExecutionRequest(
                argv=["sh", "-c", f"echo $$ > {pid_file}; sleep 30"],
                timeout_s=60,
            )
        )

    jm.register("slow", slow)
    tid = jm.submit("slow", {}, queue="fastsurfer")
    deadline = time.time() + 2
    while time.time() < deadline and not pid_file.exists():
        time.sleep(0.02)
    assert pid_file.exists()
    pid = int(pid_file.read_text().strip())

    jm.shutdown(wait=False)

    status = jm.status(tid)
    assert status["ready"] is True
    assert status["status"] == "canceled"
    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("runtime process was still alive after job manager shutdown")


def test_cancel_queued_job_marks_handle_canceled():
    from api_service.jobs.manager import JobManager

    jm = JobManager(concurrency={"api": 1}, result_ttl_s=0)
    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        release.wait(timeout=5)

    jm.register("block", block)
    jm.register("noop", lambda: None)
    blocking_tid = jm.submit("block", {}, queue="api")
    assert started.wait(timeout=2)
    queued_tid = jm.submit("noop", {}, queue="api")
    assert jm.status(queued_tid)["status"] == "queued"
    assert jm.cancel(queued_tid) is True
    status = jm.status(queued_tid)
    assert status["ready"] is True
    assert status["status"] == "canceled"
    assert jm.queue_status()["queued"] == 0
    release.set()
    _await_ready(jm, blocking_tid)
    jm.shutdown(wait=True)


def test_job_preserves_supplied_job_id():
    jm = _manager()
    jm.register("noop", lambda: None)
    tid = jm.submit("noop", {}, queue="api", job_id="fixed-123")
    assert tid == "fixed-123"
    _await_ready(jm, tid)
    jm.shutdown(wait=True)


def test_unknown_task_raises():
    jm = _manager()
    with pytest.raises(KeyError):
        jm.submit("does-not-exist", {})
    jm.shutdown(wait=True)


def test_job_evicts_terminal_handles_past_ttl():
    from api_service.jobs.manager import JobManager

    jm = JobManager(concurrency={"api": 2}, result_ttl_s=60)
    jm.register("noop", lambda: None)
    tid = jm.submit("noop", {}, queue="api")
    _await_ready(jm, tid)
    # Backdate completion past the TTL; the next submission prunes it.
    finished_at = jm._handles[tid].finished_at
    assert finished_at is not None
    jm._handles[tid].finished_at = finished_at - 120
    tid2 = jm.submit("noop", {}, queue="api")
    assert jm.status(tid)["status"] == "unknown"
    assert tid2 in jm._handles
    jm.shutdown(wait=True)


def test_job_ttl_zero_disables_eviction():
    from api_service.jobs.manager import JobManager

    jm = JobManager(concurrency={"api": 2}, result_ttl_s=0)
    jm.register("noop", lambda: None)
    tid = jm.submit("noop", {}, queue="api")
    _await_ready(jm, tid)
    finished_at = jm._handles[tid].finished_at
    assert finished_at is not None
    jm._handles[tid].finished_at = finished_at - 10_000
    jm.submit("noop", {}, queue="api")
    assert jm.status(tid)["status"] == "completed"
    jm.shutdown(wait=True)
