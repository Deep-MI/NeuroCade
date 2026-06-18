"""Expose a guarded API for launching Docker-native runtime containers."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)
app = FastAPI(title="NeuroCade Runtime Runner")


class RuntimeBindPayload(BaseModel):
    host_path: str
    container_path: str
    mode: str = "ro"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"ro", "rw"}:
            raise ValueError("Bind mode must be ro or rw")
        return value

    @field_validator("container_path")
    @classmethod
    def validate_container_path(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned.startswith("/"):
            raise ValueError("Container bind path must be absolute")
        if "," in cleaned or any(part in cleaned for part in ("\n", "\r", "\t")):
            raise ValueError("Container bind path contains unsupported characters")
        return cleaned

    @field_validator("host_path")
    @classmethod
    def validate_host_path(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned.startswith("/"):
            raise ValueError("Bind host path must be absolute")
        if "," in cleaned or any(part in cleaned for part in ("\n", "\r", "\t")):
            raise ValueError("Bind host path contains unsupported characters")
        return cleaned


class ContainerRunPayload(BaseModel):
    image: str
    command: list[str] = Field(..., min_length=1)
    binds: list[RuntimeBindPayload] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    network_disabled: bool = True
    gpu_enabled: bool = False
    remove: bool = True

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        image = str(value or "").strip().removeprefix("docker://")
        if not image or image.startswith("-") or any(ch.isspace() for ch in image):
            raise ValueError("Invalid Docker image reference")
        return image

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value or "").strip()
        if not cleaned.startswith("/"):
            raise ValueError("Container working directory must be absolute")
        if "," in cleaned or any(part in cleaned for part in ("\n", "\r", "\t")):
            raise ValueError("Container working directory contains unsupported characters")
        return cleaned


class RunCommandRequest(BaseModel):
    command: list[str] = Field(default_factory=list)
    container_run: ContainerRunPayload | None = None
    cwd: str | None = None
    timeout_s: int = Field(default=120, ge=1, le=86400)
    runtime_policy: dict | None = None


class RunCommandResponse(BaseModel):
    returncode: int
    stdout: str
    stderr: str


def _require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    token = (os.environ.get("RUNTIME_RUNNER_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="RUNTIME_RUNNER_TOKEN is required")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Invalid runtime runner token")


def _path_from_env(name: str) -> Path | None:
    value = (os.environ.get(name) or "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _container_data_root() -> Path:
    return _path_from_env("HOST_DATA_DIR") or Path("/data").resolve()


def _host_data_root() -> Path:
    return _path_from_env("NEUROCADE_HOST_DATA_DIR") or _container_data_root()


def _allowed_bind_roots() -> tuple[Path, ...]:
    configured = os.environ.get("RUNTIME_RUNNER_ALLOWED_BIND_ROOTS")
    if configured:
        roots = [Path(part).expanduser().resolve() for part in configured.split(os.pathsep) if part.strip()]
    else:
        roots = [_container_data_root()]
    return tuple(roots)


def _assert_contained(path: Path, roots: tuple[Path, ...]) -> None:
    if not roots:
        raise ValueError("Runtime runner has no allowed bind roots configured")
    for root in roots:
        try:
            path.relative_to(root)
            return
        except ValueError:
            continue
    raise ValueError(f"Bind host path is outside allowed roots: {path}")


def _remap_host_path(path_text: str, roots: tuple[Path, ...]) -> Path:
    container_path = Path(path_text).expanduser().resolve()
    _assert_contained(container_path, roots)
    data_root = _container_data_root()
    host_root = _host_data_root()
    try:
        relative = container_path.relative_to(data_root)
    except ValueError:
        return container_path
    return host_root / relative


def _docker_command(payload: ContainerRunPayload) -> list[str]:
    roots = _allowed_bind_roots()
    command = ["docker", "run"]
    if payload.remove:
        command.append("--rm")
    if payload.network_disabled:
        command.extend(["--network", "none"])
    if payload.gpu_enabled:
        command.extend(["--gpus", "all"])
    for key, value in sorted(payload.env.items()):
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ValueError(f"Invalid environment variable name: {key}")
        command.extend(["--env", f"{key}={value}"])
    for bind in payload.binds:
        host_path = _remap_host_path(bind.host_path, roots)
        mount = f"type=bind,src={host_path},dst={bind.container_path}"
        if bind.mode == "ro":
            mount += ",readonly"
        command.extend(["--mount", mount])
    if payload.cwd:
        command.extend(["--workdir", payload.cwd])
    command.append(payload.image)
    command.extend(payload.command)
    return command


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Return runner health."""
    return {"status": "ok"}


@app.post("/run", response_model=RunCommandResponse, dependencies=[Depends(_require_token)])
def run_command(payload: RunCommandRequest) -> RunCommandResponse:
    """Execute a structured Docker runtime request."""
    if payload.container_run is None:
        raise HTTPException(status_code=400, detail="container_run is required for Docker runtime execution")
    try:
        command = _docker_command(payload.container_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("runtime_runner.run timeout_s=%s command=%s", payload.timeout_s, command)
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=payload.timeout_s,
            check=False,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"Command timed out after {payload.timeout_s}s") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"Command timed out after {payload.timeout_s}s") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to execute Docker command: {exc}") from exc
    return RunCommandResponse(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
