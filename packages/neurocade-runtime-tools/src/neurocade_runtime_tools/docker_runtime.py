"""Hardened Docker argv construction for the host-native bridge."""

from __future__ import annotations

import os
from pathlib import Path

from .execution import BridgeBind, RuntimeContainerRunRequest
from .protocol import validate_environment


def build_docker_argv(request: RuntimeContainerRunRequest, *, data_root: Path) -> list[str]:
    if request.isolated and request.binds:
        raise ValueError("Isolated container execution cannot use bind mounts")
    command = [str(part) for part in request.command]
    if not command:
        raise ValueError("Docker container execution requires an explicit command")
    run_id = request.run_id or "unassigned"
    safe_id = "".join(c if c.isalnum() or c in "_.-" else "-" for c in run_id)[:80]
    argv = [
        "docker", "run", "--rm", "--name", f"neurocade-tool-{safe_id}",
        "--label", "org.neurocade.runtime=true", "--label", f"org.neurocade.run-id={run_id}",
        "--user", f"{os.getuid()}:{os.getgid()}", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev",
    ]
    if request.network_disabled:
        argv.extend(["--network", "none"])
    if request.gpu_enabled:
        argv.extend(["--gpus", "all"])
    if request.isolated:
        argv.extend(["--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m"])
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
