"""Shared runtime command execution request and result helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import subprocess
from typing import Mapping, Sequence
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300


@dataclass(slots=True)
class RuntimeExecutionPolicy:
    """Structured execution policy expected for a runtime command."""

    runtime: str = "docker"
    network_disabled: bool = True
    gpu_enabled: bool = False


@dataclass(slots=True)
class RuntimeBind:
    """Describe a bind mount for a structured runtime container request."""

    host_path: Path | str
    container_path: str
    mode: str = "ro"


RuntimeContainerBind = RuntimeBind


@dataclass(slots=True)
class RuntimeContainerRunRequest:
    """Describe a structured Docker-native runtime container execution."""

    image: str
    command: Sequence[str]
    binds: Sequence[RuntimeBind] = field(default_factory=tuple)
    env: Mapping[str, str] | None = None
    cwd: str | None = None
    network_disabled: bool = True
    gpu_enabled: bool = False
    remove: bool = True


@dataclass(slots=True)
class RuntimeArtifactIndexTarget:
    """Describe case storage that should be indexed after runtime execution."""

    user_id: str
    workspace_id: str
    case_id: str
    case_title: str | None = None
    preferred_upload_name: str | None = None


@dataclass(slots=True)
class RuntimeCaseLogArtifactTarget:
    """Describe a per-case log artifact produced by runtime execution."""

    workspace_id: str
    case_id: str
    run_id: str
    log_path: Path | str
    run_type: str


@dataclass(slots=True)
class RuntimeWorkspaceArtifactSyncTarget:
    """Describe workspace analysis storage to sync after runtime execution."""

    run_id: str
    analysis_dir: Path | str


@dataclass(slots=True)
class RuntimeCompletionHooks:
    """Describe artifact hooks that should run after runtime work completes."""

    artifact_index_targets: Sequence[RuntimeArtifactIndexTarget] = field(default_factory=tuple)
    case_log_artifact_targets: Sequence[RuntimeCaseLogArtifactTarget] = field(default_factory=tuple)
    workspace_artifact_sync_targets: Sequence[RuntimeWorkspaceArtifactSyncTarget] = field(default_factory=tuple)


@dataclass(slots=True)
class RuntimeExecutionRequest:
    """Describe a runtime command execution in one backend-facing shape."""

    argv: Sequence[str]
    cwd: Path | str | None = None
    env: Mapping[str, str] | None = None
    timeout_s: int | None = DEFAULT_TIMEOUT_SECONDS
    execution_mode: str = "local-subprocess"
    synchronous: bool = True
    queue_name: str | None = None
    task_id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    case_id: str | None = None
    output_root: Path | str | None = None
    workdir_root: Path | str | None = None
    stdout_path: Path | str | None = None
    stderr_path: Path | str | None = None
    capture_output: bool = True
    check: bool = False
    stdin_devnull: bool = True
    runtime_policy: RuntimeExecutionPolicy | None = None
    log_lines: list[str] = field(default_factory=list)
    artifact_index_targets: Sequence[RuntimeArtifactIndexTarget] = field(default_factory=tuple)
    case_log_artifact_targets: Sequence[RuntimeCaseLogArtifactTarget] = field(default_factory=tuple)
    workspace_artifact_sync_targets: Sequence[RuntimeWorkspaceArtifactSyncTarget] = field(default_factory=tuple)
    runtime_runner_url: str | None = None
    runtime_runner_token: str | None = None
    container_run: RuntimeContainerRunRequest | None = None

    @property
    def command(self) -> list[str]:
        """Return the argv as a concrete string list."""
        return [str(part) for part in self.argv]


def runtime_container_run_payload(request: RuntimeContainerRunRequest | None) -> dict[str, object] | None:
    """Return a JSON-compatible payload for a structured container request."""
    if request is None:
        return None
    return {
        "image": request.image,
        "command": [str(part) for part in request.command],
        "binds": [
            {
                "host_path": str(bind.host_path),
                "container_path": bind.container_path,
                "mode": bind.mode,
            }
            for bind in request.binds
        ],
        "env": dict(request.env or {}),
        "cwd": request.cwd,
        "network_disabled": request.network_disabled,
        "gpu_enabled": request.gpu_enabled,
        "remove": request.remove,
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
    submitted_task_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a serializable response dictionary."""
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "logs": list(self.logs),
            "execution_backend": self.execution_backend,
            "submitted_task_id": self.submitted_task_id,
        }


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
        "runtime_execution.run mode=%s sync=%s queue=%s task_id=%s cwd=%s timeout_s=%s "
        "user_id=%s workspace_id=%s case_id=%s runtime_policy=%s command=%s",
        request.execution_mode,
        request.synchronous,
        request.queue_name,
        request.task_id,
        request.cwd,
        request.timeout_s,
        request.user_id,
        request.workspace_id,
        request.case_id,
        request.runtime_policy,
        request.command if request.container_run is None else runtime_container_run_payload(request.container_run),
    )


def execute_runtime_request(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
    """Execute a runtime request through the requested execution backend."""
    if request.execution_mode == "host-runtime-runner" or request.runtime_runner_url:
        return execute_runtime_request_via_host_runner(request)
    return execute_local_runtime_request(request)


def execute_runtime_request_via_host_runner(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
    """Delegate a runtime command to a configured host runner service."""
    runner_url = (request.runtime_runner_url or "").strip().rstrip("/")
    if not runner_url:
        raise RuntimeError("host runner URL is required for host-runtime-runner execution")
    token = (request.runtime_runner_token or "").strip()
    if not token:
        raise RuntimeError("RUNTIME_RUNNER_TOKEN is required when RUNTIME_RUNNER_URL is configured")
    timeout_s = request.timeout_s if request.timeout_s is not None else DEFAULT_TIMEOUT_SECONDS
    payload = json.dumps(
        {
            "command": request.command,
            "container_run": runtime_container_run_payload(request.container_run),
            "cwd": str(request.cwd) if request.cwd is not None else None,
            "timeout_s": timeout_s,
            "runtime_policy": asdict(request.runtime_policy) if request.runtime_policy is not None else None,
        }
    ).encode("utf-8")
    http_request = urllib.request.Request(
        f"{runner_url}/run",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    _log_request(request)
    try:
        with urllib.request.urlopen(http_request, timeout=timeout_s + 5) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"host runtime runner returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"host runtime runner is unavailable at {runner_url}: {exc}") from exc

    payload_result = json.loads(body)
    return RuntimeExecutionResult(
        request=request,
        returncode=int(payload_result.get("returncode", 1)),
        stdout=str(payload_result.get("stdout") or ""),
        stderr=str(payload_result.get("stderr") or ""),
        logs=list(request.log_lines),
        execution_backend="host-runtime-runner",
    )


def execute_local_runtime_request(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
    """Execute a runtime request in a local subprocess."""
    if request.container_run is not None:
        raise RuntimeError("Structured container execution requires a configured runtime runner")
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

        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=dict(request.env) if request.env is not None else None,
                text=True,
                stdout=stdout_target,
                stderr=stderr_target,
                stdin=subprocess.DEVNULL if request.stdin_devnull else None,
                timeout=request.timeout_s,
                check=request.check,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Runtime command timed out after {request.timeout_s}s") from exc
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()

    return RuntimeExecutionResult(
        request=request,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        logs=list(request.log_lines),
        execution_backend=request.execution_mode,
    )
