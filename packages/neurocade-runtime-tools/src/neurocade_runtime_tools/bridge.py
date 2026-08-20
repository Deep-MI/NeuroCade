"""Validated process lifecycle for the host-native runtime bridge."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .apptainer_runtime import build_container_argv, nvidia_capability
from .docker_runtime import build_docker_argv
from .execution import (
    BridgeBind,
    ProcessObserver,
    RuntimeContainerRunRequest,
    _terminate_process_group,
    run_managed_command,
)
from .images import prepare_image
from .protocol import (
    ACTIVE_RUN_STATES,
    BUILD_VERSION,
    MAX_CAPTURE_BYTES,
    PROTOCOL_VERSION,
    TERMINAL_RESULT_TTL_SECONDS,
    RunState,
    RuntimeImageSpec,
    require_protocol,
    validate_environment,
    validate_relative_path,
)


def _bounded(value: str, limit: int = MAX_CAPTURE_BYTES) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value
    marker = b"\n...[runtime bridge output truncated]...\n"
    budget = max(0, limit - len(marker))
    return (encoded[: budget // 2] + marker + encoded[-(budget - budget // 2) :]).decode("utf-8", errors="replace")


def _resolve_relative(root: Path, value: str, *, label: str, must_exist: bool) -> Path:
    relative = validate_relative_path(value, label=label, allow_dot=True)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the configured data root") from exc
    if must_exist and not candidate.exists():
        raise ValueError(f"{label} does not exist")
    if candidate.exists() and not (candidate.is_file() or candidate.is_dir()):
        raise ValueError(f"{label} cannot be a device or special file")
    ancestor = candidate if candidate.exists() else next((path for path in candidate.parents if path.exists()), root)
    try:
        ancestor.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the configured data root through a symlink") from exc
    return candidate


def _container_path(value: str, *, label: str) -> str:
    path = PurePosixPath(str(value or ""))
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(value):
        raise ValueError(f"{label} must be an absolute normalized path")
    return path.as_posix()


@dataclass
class RunRecord:
    run_id: str
    request_hash: str
    backend: str
    process: subprocess.Popen[Any] | None = None
    state: RunState = RunState.accepted
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    finished_at: float | None = None
    docker_name: str | None = None
    progress: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self) -> dict[str, Any]:
        with self.lock:
            payload = {
                "protocol_version": PROTOCOL_VERSION,
                "run_id": self.run_id,
                "state": self.state.value,
                "returncode": self.returncode,
                "stdout": self.stdout,
                "stderr": self.stderr,
            }
            if self.progress is not None:
                payload["progress"] = dict(self.progress)
            return payload


class _CaptureBuffer:
    """Keep bounded head/tail text while continuously draining a pipe."""

    def __init__(self, limit: int = MAX_CAPTURE_BYTES) -> None:
        self.limit = limit
        self.head = ""
        self.tail = ""
        self.total = 0

    def append(self, value: str) -> None:
        self.total += len(value)
        half = self.limit // 2
        if len(self.head) < half:
            take = min(half - len(self.head), len(value))
            self.head += value[:take]
            value = value[take:]
        if value:
            self.tail = (self.tail + value)[-half:]

    def value(self) -> str:
        if self.total <= self.limit:
            return self.head + self.tail
        marker = f"\n...[runtime bridge output truncated; received {self.total} characters]...\n"
        budget = max(0, self.limit - len(marker))
        return self.head[: budget // 2] + marker + self.tail[-(budget - budget // 2) :]


def _drain_stream(stream: Any, capture: _CaptureBuffer) -> None:
    try:
        for chunk in iter(lambda: stream.read(8192), ""):
            capture.append(chunk)
    finally:
        stream.close()


@dataclass(frozen=True, slots=True)
class PreparedRunPaths:
    cwd: Path
    stdout: Path | None
    stderr: Path | None
    timeout: float | None
    capture_output: bool
    log_lines: tuple[str, ...]


class BridgeRuntime:
    """Validated process registry. It intentionally has no queue or persistence."""

    def __init__(self, *, backend: str, data_root: Path, image_dir: Path, terminal_ttl_s: int = TERMINAL_RESULT_TTL_SECONDS) -> None:
        if backend not in {"docker", "apptainer"}:
            raise ValueError("NEUROCADE_RUNTIME must be docker or apptainer")
        if backend == "apptainer" and os.geteuid() == 0:
            raise RuntimeError("The Apptainer bridge must run as the invoking non-root user")
        self.backend = backend
        self.data_root = data_root.expanduser().resolve(strict=True)
        self.image_dir = image_dir.expanduser().resolve()
        self.terminal_ttl_s = terminal_ttl_s
        self._runs: dict[str, RunRecord] = {}
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._auxiliary_processes: set[subprocess.Popen[str]] = set()
        self._lock = threading.Lock()
        self.global_gpu = nvidia_capability()

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.terminal_ttl_s
        expired = [key for key, run in self._runs.items() if run.finished_at is not None and run.finished_at < cutoff]
        for key in expired:
            del self._runs[key]

    @staticmethod
    def _preparation_process_observer(record: RunRecord, process: subprocess.Popen[Any] | None) -> None:
        cancel_process = False
        with record.lock:
            if process is None:
                record.process = None
            elif record.state == RunState.canceled:
                cancel_process = True
            else:
                record.process = process
        if cancel_process:
            assert process is not None
            _terminate_process_group(process)

    @staticmethod
    def _is_canceled(record: RunRecord) -> bool:
        with record.lock:
            return record.state == RunState.canceled

    @staticmethod
    def _register_running_process(record: RunRecord, process: subprocess.Popen[Any]) -> bool:
        with record.lock:
            if record.state == RunState.canceled:
                return False
            record.process = process
            record.state = RunState.running
            return True

    def _auxiliary_process_observer(self) -> ProcessObserver:
        observed: list[subprocess.Popen[str]] = []

        def observe(process: subprocess.Popen[str] | None) -> None:
            with self._lock:
                if process is None:
                    if observed:
                        self._auxiliary_processes.discard(observed.pop())
                else:
                    observed.append(process)
                    self._auxiliary_processes.add(process)

        return observe

    def health(self) -> dict[str, Any]:
        with self._lock:
            self._prune()
            active = sum(run.state in ACTIVE_RUN_STATES for run in self._runs.values())
        return {
            "protocol_version": PROTOCOL_VERSION,
            "build_version": BUILD_VERSION,
            "backend": self.backend,
            "architecture": platform.machine(),
            "gpu": {"available": self.global_gpu.available, "reason": self.global_gpu.reason},
            "active_runs": active,
        }

    def resolve_capability(self, image_value: Any) -> dict[str, Any]:
        spec = RuntimeImageSpec.from_dict(image_value) if isinstance(image_value, dict) else RuntimeImageSpec(str(image_value))
        key = json.dumps(spec.to_dict(), sort_keys=True)
        with self._lock:
            cached = self._capabilities.get(key)
        if cached is not None:
            return cached
        if not self.global_gpu.available:
            result = {
                "protocol_version": PROTOCOL_VERSION,
                "cpu": True,
                "cuda": False,
                "reason": self.global_gpu.reason,
            }
            with self._lock:
                self._capabilities[key] = result
            return result
        process_observer = self._auxiliary_process_observer()
        prepared = prepare_image(
            spec,
            backend=self.backend,
            image_dir=self.image_dir,
            process_observer=process_observer,
        )
        probe = RuntimeContainerRunRequest(
            image=spec,
            command=["python3", "-c", "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"],
            network_disabled=True,
            gpu_enabled=True,
            run_id="capability-probe-" + hashlib.sha256(key.encode()).hexdigest()[:16],
        )
        argv = (
            build_docker_argv(probe, data_root=self.data_root)
            if self.backend == "docker"
            else build_container_argv(probe, data_root=self.data_root, prepared_image=prepared)
        )
        try:
            completed = run_managed_command(
                argv,
                timeout=30,
                capture_output=True,
                process_observer=process_observer,
            )
            available = completed.returncode == 0
            reason = (
                "CUDA initialized inside the tool image"
                if available
                else (completed.stderr or completed.stdout).strip() or "CUDA did not initialize inside the tool image"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            available = False
            reason = f"Tool image CUDA probe failed: {exc}"
        result = {
            "protocol_version": PROTOCOL_VERSION,
            "cpu": True,
            "cuda": available,
            "reason": reason,
        }
        with self._lock:
            self._capabilities[key] = result
        return result

    def _validated_request(self, payload: dict[str, Any]) -> tuple[RuntimeContainerRunRequest, PreparedRunPaths]:
        require_protocol(payload)
        run_id = str(payload.get("run_id") or "")
        if not run_id or len(run_id) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in run_id):
            raise ValueError("Invalid run ID")
        container = payload.get("container")
        if not isinstance(container, dict):
            raise ValueError("container is required")
        image_value = container.get("image")
        spec = RuntimeImageSpec.from_dict(image_value) if isinstance(image_value, dict) else RuntimeImageSpec(str(image_value))
        command = container.get("command")
        if not isinstance(command, list) or not command or any("\x00" in str(part) for part in command):
            raise ValueError("Container command must be a non-empty argument array")
        isolated = bool(container.get("isolated", False))
        binds: list[BridgeBind] = []
        for value in container.get("binds", []):
            if not isinstance(value, dict) or value.get("mode") not in {"ro", "rw"}:
                raise ValueError("Invalid runtime bind")
            source_relative = validate_relative_path(str(value.get("source_relative") or ""), label="Bind source", allow_dot=True)
            source = _resolve_relative(self.data_root, source_relative, label="Bind source", must_exist=True)
            if not source.is_dir() and not source.is_file():
                raise ValueError("Bind source must be a regular file or directory")
            binds.append(
                BridgeBind(source_relative, _container_path(str(value.get("container_path") or ""), label="Bind target"), value["mode"])
            )
        if isolated and (binds or container.get("gpu_enabled")):
            raise ValueError("Isolated runs cannot use binds or GPUs")
        request = RuntimeContainerRunRequest(
            image=spec,
            command=[str(part) for part in command],
            binds=binds,
            env=validate_environment(dict(container.get("env") or {})),
            cwd=_container_path(container["cwd"], label="Container working directory") if container.get("cwd") else None,
            network_disabled=bool(container.get("network_disabled", True)),
            gpu_enabled=bool(container.get("gpu_enabled", False)),
            isolated=isolated,
            run_id=run_id,
        )
        timeout = float(payload["timeout_s"]) if payload.get("timeout_s") is not None else None
        if timeout is not None and (timeout <= 0 or timeout > 7 * 24 * 3600):
            raise ValueError("Runtime timeout is outside the allowed range")
        paths = PreparedRunPaths(
            cwd=(
                _resolve_relative(self.data_root, payload["workdir_relative"], label="Working directory", must_exist=True)
                if payload.get("workdir_relative")
                else self.data_root
            ),
            stdout=(
                _resolve_relative(self.data_root, payload["stdout_relative"], label="stdout log", must_exist=False)
                if payload.get("stdout_relative")
                else None
            ),
            stderr=(
                _resolve_relative(self.data_root, payload["stderr_relative"], label="stderr log", must_exist=False)
                if payload.get("stderr_relative")
                else None
            ),
            timeout=timeout,
            capture_output=bool(payload.get("capture_output", True)),
            log_lines=tuple(str(line) for line in payload.get("log_lines", [])),
        )
        return request, paths

    def start(self, payload: dict[str, Any]) -> tuple[RunRecord, bool]:
        request, paths = self._validated_request(payload)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request_hash = hashlib.sha256(canonical).hexdigest()
        assert request.run_id is not None
        with self._lock:
            self._prune()
            existing = self._runs.get(request.run_id)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise FileExistsError("Run ID already exists with a different request")
                return existing, False
            record = RunRecord(request.run_id, request_hash, self.backend)
            self._runs[request.run_id] = record
        threading.Thread(
            target=self._launch,
            args=(record, request, paths),
            name=f"bridge-prepare-{request.run_id}",
            daemon=True,
        ).start()
        return record, True

    def _launch(self, record: RunRecord, request: RuntimeContainerRunRequest, paths: PreparedRunPaths) -> None:
        """Prepare the image and launch asynchronously so POST remains responsive."""
        stdout_handle = stderr_handle = None
        try:
            def update_progress(payload: dict[str, Any]) -> None:
                with record.lock:
                    previous = record.progress or {}
                    previous_value = previous.get("progress")
                    next_value = payload.get("progress")
                    if (
                        previous.get("phase") == payload.get("phase")
                        and previous.get("total_bytes") == payload.get("total_bytes")
                        and isinstance(previous_value, (int, float))
                        and isinstance(next_value, (int, float))
                    ):
                        payload["progress"] = max(float(previous_value), float(next_value))
                    payload["updated_at"] = time.time()
                    record.progress = dict(payload)

            prepared = prepare_image(
                request.image,
                backend=self.backend,
                image_dir=self.image_dir,
                process_observer=lambda process: self._preparation_process_observer(record, process),
                is_cancelled=lambda: self._is_canceled(record),
                progress_observer=update_progress,
            )
            with record.lock:
                if record.state == RunState.canceled:
                    record.finished_at = time.monotonic()
                    return
            argv = (
                build_docker_argv(request, data_root=self.data_root)
                if self.backend == "docker"
                else build_container_argv(request, data_root=self.data_root, prepared_image=prepared)
            )
            if self.backend == "docker":
                record.docker_name = f"neurocade-tool-{request.run_id}"[:95]
            stdout_target: Any = subprocess.PIPE if paths.capture_output and paths.stdout is None else subprocess.DEVNULL
            stderr_target: Any = subprocess.PIPE if paths.capture_output and paths.stderr is None else subprocess.DEVNULL
            if paths.stdout is not None:
                paths.stdout.parent.mkdir(parents=True, exist_ok=True)
                stdout_handle = paths.stdout.open("w", encoding="utf-8")
                if paths.log_lines:
                    stdout_handle.write("".join(line if line.endswith("\n") else line + "\n" for line in paths.log_lines))
                    stdout_handle.flush()
                stdout_target = stdout_handle
            if paths.stderr is not None:
                paths.stderr.parent.mkdir(parents=True, exist_ok=True)
                stderr_handle = paths.stderr.open("w", encoding="utf-8")
                stderr_target = stderr_handle
            process = subprocess.Popen(
                argv, cwd=paths.cwd, text=True, stdin=subprocess.DEVNULL,
                stdout=stdout_target, stderr=stderr_target, start_new_session=True,
            )
            if not self._register_running_process(record, process):
                _terminate_process_group(process)
                process.communicate()
                return
            stdout_capture = _CaptureBuffer() if process.stdout is not None else None
            stderr_capture = _CaptureBuffer() if process.stderr is not None else None
            capture_threads = []
            for stream, capture in ((process.stdout, stdout_capture), (process.stderr, stderr_capture)):
                if stream is not None and capture is not None:
                    thread = threading.Thread(target=_drain_stream, args=(stream, capture), daemon=True)
                    thread.start()
                    capture_threads.append(thread)
            threading.Thread(
                target=self._watch,
                args=(record, paths.timeout, stdout_handle, stderr_handle, stdout_capture, stderr_capture, capture_threads),
                name=f"bridge-run-{request.run_id}", daemon=True,
            ).start()
        except Exception as exc:  # noqa: BLE001 - expose backend failure through run state
            if stdout_handle is not None:
                stdout_handle.close()
            if stderr_handle is not None:
                stderr_handle.close()
            with record.lock:
                record.returncode = 1
                record.stderr = _bounded(str(exc))
                record.state = RunState.canceled if record.state == RunState.canceled else RunState.failed
                record.finished_at = time.monotonic()

    def _watch(
        self,
        record: RunRecord,
        timeout: float | None,
        stdout_handle: Any,
        stderr_handle: Any,
        stdout_capture: _CaptureBuffer | None,
        stderr_capture: _CaptureBuffer | None,
        capture_threads: list[threading.Thread],
    ) -> None:
        process = record.process
        assert process is not None
        terminal = RunState.completed
        try:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                terminal = RunState.timed_out
                _terminate_process_group(process)
                process.wait()
            for thread in capture_threads:
                thread.join(timeout=5)
            with record.lock:
                if record.state == RunState.canceled:
                    terminal = RunState.canceled
                elif terminal != RunState.timed_out and process.returncode != 0:
                    terminal = RunState.failed
                record.returncode = process.returncode
                record.stdout = stdout_capture.value() if stdout_capture is not None else ""
                record.stderr = stderr_capture.value() if stderr_capture is not None else ""
                record.state = terminal
                record.finished_at = time.monotonic()
                record.process = None
        finally:
            if stdout_handle is not None:
                stdout_handle.close()
            if stderr_handle is not None:
                stderr_handle.close()

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            self._prune()
            return self._runs.get(run_id)

    def cancel(self, run_id: str) -> RunRecord | None:
        record = self.get(run_id)
        if record is None:
            return None
        with record.lock:
            process = record.process
            was_active = record.state in ACTIVE_RUN_STATES
            if was_active:
                record.state = RunState.canceled
                if process is None:
                    record.finished_at = time.monotonic()
        if was_active and record.docker_name:
            run_managed_command(["docker", "rm", "-f", record.docker_name], timeout=30, capture_output=True)
        if process is not None:
            _terminate_process_group(process)
        return record

    def shutdown(self) -> None:
        with self._lock:
            ids = list(self._runs)
            auxiliary_processes = list(self._auxiliary_processes)
        for run_id in ids:
            self.cancel(run_id)
        for process in auxiliary_processes:
            _terminate_process_group(process)
