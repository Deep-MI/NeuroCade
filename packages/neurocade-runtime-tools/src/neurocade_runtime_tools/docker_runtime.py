"""Hardened Docker argv construction for the host-native bridge."""

from __future__ import annotations

import os
import platform as host_platform
import re
from pathlib import Path

from .execution import BridgeBind, RuntimeContainerRunRequest
from .protocol import validate_environment

_DOCKER_PLATFORM = re.compile(
    r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?$"
)


def configured_docker_platform() -> str | None:
    """Return the launcher's validated application-container platform."""
    value = os.environ.get("NEUROCADE_DOCKER_PLATFORM", "").strip().lower()
    if not value:
        return None
    if not _DOCKER_PLATFORM.fullmatch(value):
        raise ValueError("NEUROCADE_DOCKER_PLATFORM must use os/architecture[/variant] syntax")
    return value


def detected_docker_host_platform() -> str:
    """Return the native Linux platform Docker uses for the local host."""
    architecture = host_platform.machine().strip().lower()
    architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(architecture, architecture)
    if not architecture or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", architecture):
        raise RuntimeError(f"Unsupported host architecture reported by the operating system: {architecture!r}")
    return f"linux/{architecture}"


def build_docker_argv(
    request: RuntimeContainerRunRequest,
    *,
    data_root: Path,
    platform: str,
    launch_id: str | None = None,
) -> list[str]:
    if request.isolated and request.binds:
        raise ValueError("Isolated container execution cannot use bind mounts")
    command = [str(part) for part in request.command]
    if not command:
        raise ValueError("Docker container execution requires an explicit command")
    run_id = request.run_id or "unassigned"
    safe_id = "".join(c if c.isalnum() or c in "_.-" else "-" for c in run_id)[:80]
    if not _DOCKER_PLATFORM.fullmatch(platform):
        raise ValueError("Docker tool platform must use os/architecture[/variant] syntax")
    argv = ["docker", "run", "--platform", platform]
    argv.extend(
        [
            "--rm",
            "--name",
            f"neurocade-tool-{safe_id}",
            "--label",
            "org.neurocade.runtime=true",
            "--label",
            f"org.neurocade.run-id={run_id}",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev",
        ]
    )
    if launch_id:
        argv.extend(["--label", f"org.neurocade.launch-id={launch_id}"])
    if request.network_disabled:
        argv.extend(["--network", "none"])
    if request.gpu_enabled:
        argv.extend(["--gpus", "all"])
    if request.isolated:
        argv.extend(["--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m"])
    for path in request.scratch_paths:
        # Docker Desktop reports bind roots as root-owned even when the macOS
        # source belongs to the caller. A private tmpfs gives tools an honest,
        # user-owned parent without weakening host directory permissions.
        options = f"{path}:rw,nosuid,nodev,noexec,uid={os.getuid()},gid={os.getgid()},mode=0755"
        argv.extend(["--tmpfs", options])
    for bind in request.binds:
        if not isinstance(bind, BridgeBind):
            raise TypeError("Docker adapter requires root-relative bridge binds")
        source = (data_root / bind.source_relative).resolve()
        source.relative_to(data_root)
        mount = f"type=bind,src={source},dst={bind.container_path}"
        if bind.mode == "ro":
            mount += ",readonly"
        argv.extend(["--mount", mount])
    if request.cwd:
        argv.extend(["--workdir", request.cwd])
    for key, value in sorted(validate_environment(dict(request.env or {})).items()):
        argv.extend(["--env", f"{key}={value}"])
    # Runtime requests describe a complete command, not arguments for an
    # image-defined entrypoint. Override the entrypoint so tools such as
    # FastSurfer do not interpret `/bin/bash` as one of their own flags.
    argv.extend(["--entrypoint", command[0]])
    argv.append(request.image.docker_reference)
    argv.extend(command[1:])
    return argv
