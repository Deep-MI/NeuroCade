"""Expose a guarded API for launching allowlisted host runtime commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from neurocade_runtime_tools.execution import RuntimeExecutionPolicy, RuntimeExecutionRequest, execute_runtime_request


logger = logging.getLogger(__name__)
app = FastAPI(title="NeuroCade Host Runtime Runner")


class RunRuntimePolicy(BaseModel):
    runtime: str = "apptainer"
    network_disabled: bool = True
    gpu_enabled: bool = False


class RunCommandRequest(BaseModel):
    command: list[str] = Field(..., min_length=1)
    cwd: str | None = None
    timeout_s: int = Field(default=120, ge=1, le=86400)
    runtime_policy: RunRuntimePolicy | None = None

    @field_validator("command")
    @classmethod
    def validate_runtime_command(cls, command: list[str]) -> list[str]:
        """Require an allowlisted bare runtime executable."""
        allowed = {
            name.strip()
            for name in os.environ.get("HOST_RUNTIME_RUNNER_ALLOWED_BINS", "apptainer,singularity").split(",")
            if name.strip()
        }
        executable = str(command[0])
        if Path(executable).name != executable:
            raise ValueError("Runtime executable must be an allowlisted bare command name")
        if executable not in allowed:
            allowed_display = ", ".join(sorted(allowed)) or "<none>"
            raise ValueError(f"Unsupported runtime executable '{executable}'. Allowed: {allowed_display}")
        return [str(part) for part in command]


class RunCommandResponse(BaseModel):
    returncode: int
    stdout: str
    stderr: str


def _allowed_bind_roots() -> tuple[Path, ...]:
    """Return resolved host bind roots allowed for delegated runtime commands."""
    configured = os.environ.get("HOST_RUNTIME_RUNNER_ALLOWED_BIND_ROOTS")
    raw_roots = (
        configured.split(os.pathsep)
        if configured is not None
        else [os.environ.get("HOST_DATA_DIR", "")]
    )
    roots: list[Path] = []
    for raw_root in raw_roots:
        if not raw_root.strip():
            continue
        root = Path(raw_root).expanduser().resolve()
        if root.is_dir():
            roots.append(root)
    return tuple(roots)


def _bind_host_path(bind_spec: str) -> Path:
    """Extract and resolve the host path from one Apptainer bind specification."""
    if "," in bind_spec:
        raise ValueError("Comma-separated bind lists are not supported by the host runtime runner")
    host_path = bind_spec.split(":", 1)[0].strip()
    if not host_path:
        raise ValueError("Bind host path cannot be empty")
    return Path(host_path).expanduser().resolve()


def _assert_bind_allowed(host_path: Path, allowed_roots: tuple[Path, ...]) -> None:
    if not allowed_roots:
        raise ValueError("Host runtime runner has no allowed bind roots configured")
    for root in allowed_roots:
        try:
            host_path.relative_to(root)
            return
        except ValueError:
            continue
    allowed = ", ".join(str(root) for root in allowed_roots)
    raise ValueError(f"Bind host path is outside allowed roots: {host_path}. Allowed roots: {allowed}")


def _validate_bind_paths(command: list[str]) -> None:
    """Constrain host-side bind paths before executing a delegated runtime command."""
    allowed_roots = _allowed_bind_roots()
    if len(command) < 2 or command[1] != "exec":
        return
    flags = {"--net", "--cleanenv", "--no-home", "--nv", "--nvccli", "--quiet"}
    options_with_values = {"--network", "--bind", "--pwd", "--no-mount"}
    index = 2
    while index < len(command):
        token = str(command[index])
        if not token.startswith("-"):
            return
        if token == "--bind":
            if index + 1 >= len(command):
                raise ValueError("Apptainer --bind requires a value")
            _assert_bind_allowed(_bind_host_path(str(command[index + 1])), allowed_roots)
            index += 2
            continue
        if token.startswith("--bind="):
            _assert_bind_allowed(_bind_host_path(token.removeprefix("--bind=")), allowed_roots)
            index += 1
            continue
        if token in flags:
            index += 1
            continue
        if token in options_with_values:
            index += 2
            continue
        index += 1


def _host_runtime_command(command: list[str]) -> list[str]:
    """Route singularity invocations through the configured Apptainer binary."""
    if Path(command[0]).name == "singularity" and os.environ.get("APPTAINER_BIN"):
        return [os.environ["APPTAINER_BIN"], *command[1:]]
    return command


def _require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """Reject requests without the configured bearer token."""
    token = (os.environ.get("HOST_RUNTIME_RUNNER_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="HOST_RUNTIME_RUNNER_TOKEN is required")
    expected = f"Bearer {token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid host runtime runner token")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Return the runner service health status."""
    return {"status": "ok"}


@app.post("/run", response_model=RunCommandResponse, dependencies=[Depends(_require_token)])
def run_command(payload: RunCommandRequest) -> RunCommandResponse:
    """Execute a validated host runtime command and return captured output."""
    cwd = None
    if payload.cwd:
        cwd_path = Path(payload.cwd).expanduser().resolve()
        if not cwd_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Working directory does not exist: {cwd_path}")
        cwd = cwd_path

    command = _host_runtime_command(payload.command)
    logger.info("host_runtime_runner.run command=%s cwd=%s timeout_s=%s", command, cwd, payload.timeout_s)
    try:
        if payload.runtime_policy is None:
            raise HTTPException(status_code=400, detail="runtime_policy is required")
        _validate_bind_paths(command)
        policy = RuntimeExecutionPolicy(
            runtime=payload.runtime_policy.runtime,
            network_disabled=payload.runtime_policy.network_disabled,
            gpu_enabled=payload.runtime_policy.gpu_enabled,
        )
        result = execute_runtime_request(
            RuntimeExecutionRequest(
                argv=command,
                cwd=cwd,
                timeout_s=payload.timeout_s,
                execution_mode="host-runtime-runner-adapter",
                require_rootless_apptainer=True,
                runtime_policy=policy,
            )
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"Command timed out after {payload.timeout_s}s") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute command: {exc}") from exc

    return RunCommandResponse(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
