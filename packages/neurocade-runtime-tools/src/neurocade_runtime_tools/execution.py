"""Shared runtime command execution request and result helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import RuntimeImageSpec

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300
TERMINATION_GRACE_SECONDS = 5.0

# Runtime-agnostic cancellation registration. Executors publish a callback that
# the JobManager may invoke without knowing whether it controls a process group
# or an authenticated bridge run.
cancellation_observer: ContextVar[Callable[[Callable[[], None]], None] | None] = ContextVar(
    "runtime_cancellation_observer", default=None
)

ProcessObserver = Callable[[subprocess.Popen[str] | None], None]
ProgressObserver = Callable[[dict[str, Any]], None]
runtime_progress_observer: ContextVar[ProgressObserver | None] = ContextVar(
    "runtime_progress_observer", default=None
)


@dataclass(slots=True)
class RuntimeBind:
    """Describe a bind mount for a structured runtime container request."""

    host_path: Path | str
    container_path: str
    mode: str = "ro"


@dataclass(frozen=True, slots=True)
class BridgeBind:
    """A validated bind path relative to the bridge's configured data root."""

    source_relative: str
    container_path: str
    mode: str = "ro"


@dataclass(slots=True)
class RuntimeContainerRunRequest:
    """Describe a structured container execution for either bridge backend."""

    image: RuntimeImageSpec
    command: Sequence[str]
    binds: Sequence[RuntimeBind | BridgeBind] = field(default_factory=tuple)
    scratch_paths: Sequence[str] = field(default_factory=tuple)
    env: Mapping[str, str] | None = None
    cwd: str | None = None
    network_disabled: bool = True
    gpu_enabled: bool = False
    isolated: bool = False
    run_id: str | None = None


@dataclass(slots=True)
class RuntimeExecutionRequest:
    """Describe a runtime command execution in one backend-facing shape."""

    argv: Sequence[str] = field(default_factory=tuple)
    cwd: Path | str | None = None
    env: Mapping[str, str] | None = None
    timeout_s: float | None = DEFAULT_TIMEOUT_SECONDS
    execution_mode: str = "local-subprocess"
    output_root: Path | str | None = None
    workdir_root: Path | str | None = None
    stdout_path: Path | str | None = None
    stderr_path: Path | str | None = None
    capture_output: bool = True
    check: bool = False
    stdin_devnull: bool = True
    log_lines: list[str] = field(default_factory=list)
    container_run: RuntimeContainerRunRequest | None = None

    def __post_init__(self) -> None:
        if bool(self.argv) == (self.container_run is not None):
            raise ValueError("Provide exactly one of argv or container_run")

    @property
    def command(self) -> list[str]:
        """Return the argv as a concrete string list."""
        return [str(part) for part in self.argv]


def runtime_container_run_payload(request: RuntimeContainerRunRequest | None) -> dict[str, object] | None:
    """Return a JSON-compatible payload for a structured container request."""
    if request is None:
        return None
    return {
        "image": request.image.to_dict(),
        "command": [str(part) for part in request.command],
        "binds": [
            {
                "source": str(bind.host_path if isinstance(bind, RuntimeBind) else bind.source_relative),
                "container_path": bind.container_path,
                "mode": bind.mode,
            }
            for bind in request.binds
        ],
        "scratch_paths": list(request.scratch_paths),
        "env": dict(request.env or {}),
        "cwd": request.cwd,
        "network_disabled": request.network_disabled,
        "gpu_enabled": request.gpu_enabled,
        "isolated": request.isolated,
        "run_id": request.run_id,
    }


@dataclass(slots=True)
class RuntimeExecutionResult:
    """Capture a runtime command execution result."""

    request: RuntimeExecutionRequest
    returncode: int
    stdout: str = ""
    stderr: str = ""
    logs: list[str] = field(default_factory=list)
    execution_backend: str = "local-subprocess"


def _resolved_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser().resolve()


def _assert_contained(path: Path, root: Path, *, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay under {root}: {path}") from exc


def _assert_request_containment(request: RuntimeExecutionRequest) -> None:
    workdir_root = _resolved_path(request.workdir_root)
    cwd = _resolved_path(request.cwd)
    if workdir_root is not None and cwd is not None:
        _assert_contained(cwd, workdir_root, label="Working directory")

    output_root = _resolved_path(request.output_root)
    if output_root is None:
        return
    for label, path_value in (("stdout log", request.stdout_path), ("stderr log", request.stderr_path)):
        path = _resolved_path(path_value)
        if path is not None:
            _assert_contained(path, output_root, label=label)


def _log_request(request: RuntimeExecutionRequest) -> None:
    logger.info(
        "runtime_execution.run mode=%s cwd=%s timeout_s=%s command=%s",
        request.execution_mode,
        request.cwd,
        request.timeout_s,
        request.command if request.container_run is None else runtime_container_run_payload(request.container_run),
    )


def run_managed_command(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    check: bool = False,
    capture_output: bool = False,
    process_observer: ProcessObserver | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one command in a managed process group with timeout and interruption cleanup."""
    command = [str(part) for part in argv]
    if not command:
        raise ValueError("Managed command cannot be empty")
    process = subprocess.Popen(
        command,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        start_new_session=True,
    )
    try:
        if process_observer is not None:
            process_observer(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            assert timeout is not None
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from exc
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command, stdout, stderr)
        return result
    except BaseException:
        _terminate_process_group(process)
        raise
    finally:
        if process_observer is not None:
            process_observer(None)


def execute_runtime_request(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
    """Execute locally trusted commands locally and every container via the bridge."""
    if request.container_run is not None:
        from .bridge_client import BridgeClient

        if request.container_run.run_id is None:
            request.container_run.run_id = str(uuid.uuid4())
        return BridgeClient.from_environment().execute(request)
    return execute_local_runtime_request(request)


async def execute_runtime_request_async(
    request: RuntimeExecutionRequest,
    *,
    progress_observer: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> RuntimeExecutionResult:
    """Execute a runtime request without leaking work when its asyncio task is canceled.

    ``asyncio.to_thread`` does not stop its worker thread when the awaiting task is
    canceled. Register the runtime's backend-neutral cancellation callback before
    entering the thread so assistant timeouts and disconnected clients also stop
    bridge image preparation or the launched container.
    """
    callback_lock = threading.Lock()
    cancel_requested = threading.Event()
    cancel_callback: Callable[[], None] | None = None

    def observe_cancellation(callback: Callable[[], None]) -> None:
        nonlocal cancel_callback
        with callback_lock:
            cancel_callback = callback
        if cancel_requested.is_set():
            callback()

    loop = asyncio.get_running_loop()

    def publish_progress(payload: dict[str, Any]) -> None:
        if progress_observer is None:
            return
        observer = progress_observer

        def schedule() -> None:
            async def deliver() -> None:
                await observer(dict(payload))

            asyncio.create_task(deliver())

        loop.call_soon_threadsafe(schedule)

    cancellation_token = cancellation_observer.set(observe_cancellation)
    progress_token = runtime_progress_observer.set(publish_progress)
    task = asyncio.create_task(asyncio.to_thread(execute_runtime_request, request))
    try:
        return await task
    except asyncio.CancelledError:
        cancel_requested.set()
        with callback_lock:
            callback = cancel_callback
        if callback is not None:
            await asyncio.to_thread(callback)
        raise
    finally:
        runtime_progress_observer.reset(progress_token)
        cancellation_observer.reset(cancellation_token)


def execute_local_runtime_request(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
    """Execute a runtime request in a local subprocess.

    Uses ``Popen`` with a dedicated process group so the in-process JobWorker can
    terminate a long-running tool and its children on cancellation.
    """
    command = request.command
    if not command:
        raise ValueError("Runtime execution command cannot be empty")
    _assert_request_containment(request)
    _log_request(request)

    cwd = _resolved_path(request.cwd)
    stdout_handle = None
    stderr_handle = None
    try:
        stdout_target = subprocess.PIPE if request.capture_output and request.stdout_path is None else None
        stderr_target = subprocess.PIPE if request.capture_output and request.stderr_path is None else None
        if request.stdout_path is not None:
            stdout_path = _resolved_path(request.stdout_path)
            assert stdout_path is not None
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_path.open("w", encoding="utf-8")
            if request.log_lines:
                stdout_handle.write("".join(line if line.endswith("\n") else f"{line}\n" for line in request.log_lines))
                stdout_handle.flush()
            stdout_target = stdout_handle
        if request.stderr_path is not None:
            stderr_path = _resolved_path(request.stderr_path)
            assert stderr_path is not None
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_handle = stderr_path.open("w", encoding="utf-8")
            stderr_target = stderr_handle

        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(request.env) if request.env is not None else None,
            text=True,
            stdout=stdout_target,
            stderr=stderr_target,
            stdin=subprocess.DEVNULL if request.stdin_devnull else None,
            start_new_session=True,
        )
        cancel_observer = cancellation_observer.get()
        if cancel_observer is not None:
            cancel_observer(lambda: _terminate_process_group(process))
        try:
            stdout_text, stderr_text = process.communicate(timeout=request.timeout_s)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise TimeoutError(f"Runtime command timed out after {request.timeout_s}s") from exc
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()

    returncode = process.returncode
    if request.check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, command, stdout_text, stderr_text)
    return RuntimeExecutionResult(
        request=request,
        returncode=returncode,
        stdout=stdout_text or "",
        stderr=stderr_text or "",
        logs=list(request.log_lines),
        execution_backend=request.execution_mode,
    )


def _kill_process_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()


def _terminate_process_group(process: subprocess.Popen, *, grace_s: float | None = None) -> None:
    """Terminate a process group, escalating to SIGKILL if TERM is ignored."""
    grace_s = TERMINATION_GRACE_SECONDS if grace_s is None else grace_s
    if process.poll() is not None:
        return
    _kill_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        logger.warning("runtime_execution.terminate_escalated pid=%s grace_s=%s", process.pid, grace_s)

    _kill_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        logger.error("runtime_execution.kill_timeout pid=%s grace_s=%s", process.pid, grace_s)
