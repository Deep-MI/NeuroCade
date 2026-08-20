"""Rootless Apptainer adapter for the host-native runtime bridge."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .execution import BridgeBind, RuntimeContainerRunRequest, run_managed_command
from .protocol import validate_environment


@dataclass(frozen=True, slots=True)
class NvidiaCapability:
    available: bool
    reason: str


def nvidia_capability() -> NvidiaCapability:
    """Probe host devices, driver utility, and libcuda as the invoking user."""
    if not Path("/dev/nvidiactl").exists():
        return NvidiaCapability(False, "NVIDIA device nodes are not available")
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return NvidiaCapability(False, "nvidia-smi is not available on the host")
    try:
        result = run_managed_command([executable, "-L"], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return NvidiaCapability(False, f"nvidia-smi could not run: {exc}")
    if result.returncode != 0:
        return NvidiaCapability(False, (result.stderr or result.stdout).strip() or "nvidia-smi did not detect a GPU")
    try:
        ctypes.CDLL("libcuda.so.1")
    except OSError:
        return NvidiaCapability(False, "NVIDIA driver library libcuda.so.1 is not available")
    return NvidiaCapability(True, next((line.strip() for line in result.stdout.splitlines() if line.strip()), "NVIDIA GPU"))


def _container_path(value: str, *, label: str) -> str:
    path = PurePosixPath(str(value or ""))
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(value) or "," in str(value):
        raise ValueError(f"{label} must be an absolute normalized path")
    return path.as_posix()


def _assert_rootless(request: RuntimeContainerRunRequest) -> None:
    for token in request.command:
        value = str(token)
        if value in {"--fakeroot", "--writable", "--writable-tmpfs", "sudo"} or value.startswith("--fakeroot"):
            raise ValueError(f"Disallowed privilege escalation in tool command: {value}")
    if request.isolated and (request.binds or request.gpu_enabled):
        raise ValueError("Isolated Apptainer runs cannot use binds or a GPU")


def build_container_argv(
    request: RuntimeContainerRunRequest,
    *,
    data_root: Path,
    prepared_image: Path | str,
) -> list[str]:
    """Build a shell-free argv for a verified, immutable SIF."""
    _assert_rootless(request)
    root = data_root.expanduser().resolve(strict=True)
    image = Path(prepared_image).expanduser().resolve(strict=True)
    if not image.is_file():
        raise ValueError("Prepared Apptainer image must be a regular SIF file")
    argv = ["apptainer", "--quiet", "exec", "--cleanenv", "--no-home", "--containall"]
    if request.isolated:
        argv.extend(["--no-mount", "hostfs,cwd"])
    if request.network_disabled:
        argv.extend(["--net", "--network", "none"])
    if request.gpu_enabled:
        argv.append("--nv")
    for bind in request.binds:
        if not isinstance(bind, BridgeBind):
            raise TypeError("Apptainer adapter requires root-relative bridge binds")
        if bind.mode not in {"ro", "rw"}:
            raise ValueError(f"Unsupported bind mode: {bind.mode}")
        source = (root / bind.source_relative).resolve(strict=True)
        source.relative_to(root)
        target = _container_path(bind.container_path, label="Bind target")
        specification = f"{source}:{target}" + (":ro" if bind.mode == "ro" else "")
        argv.extend(["--bind", specification])
    if request.cwd:
        argv.extend(["--pwd", _container_path(request.cwd, label="Container working directory")])
    for key, value in sorted(validate_environment(dict(request.env or {})).items()):
        argv.extend(["--env", f"{key}={value}"])
    argv.append(str(image))
    argv.extend(str(part) for part in request.command)
    return argv
