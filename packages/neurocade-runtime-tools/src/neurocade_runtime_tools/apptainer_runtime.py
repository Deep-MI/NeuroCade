"""Apptainer execution for NeuroCade workflow containers.

The monolith launches analysis tools (including FastSurfer and dcm2niix) by
turning a structured :class:`RuntimeContainerRunRequest` into a concrete
``argv`` that is run as a local subprocess.

``apptainer exec`` needs no daemon or socket and runs in both native and
privileged-container deployments. Images resolve to a prepared, arch-matched SIF
when available, otherwise Apptainer receives the corresponding ``docker://`` URI.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .container_request import _validate_container_path as _validate_path
from .container_request import container_image_name
from .execution import RuntimeContainerRunRequest

SIF_DIR_ENV = "NEUROCADE_SIF_DIR"
GPU_MODE_ENV = "NEUROCADE_GPU_MODE"


class RuntimeGpuUnavailableError(RuntimeError):
    """Raised when CUDA was explicitly requested but is unavailable."""


@dataclass(frozen=True, slots=True)
class NvidiaCapability:
    """Describe whether the current process can launch CUDA workloads."""

    available: bool
    reason: str


def _normalise_arch() -> str:
    """Return the host CPU architecture as ``amd64`` or ``arm64``."""
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def apptainer_sif_path(image: str, *, sif_dir: str | Path | None = None) -> Path:
    """Return the persistent, architecture-specific SIF path for an OCI image."""
    root = Path(sif_dir or os.environ.get(SIF_DIR_ENV) or ".").expanduser()
    stem = container_image_name(image).replace("/", "_").replace(":", "_")
    return root / f"{stem}-{_normalise_arch()}.sif"


def nvidia_capability() -> NvidiaCapability:
    """Probe devices, driver utilities, and libcuda in the current runtime."""
    if not Path("/dev/nvidiactl").exists():
        return NvidiaCapability(False, "NVIDIA device nodes are not available")
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return NvidiaCapability(False, "nvidia-smi is not available in the NeuroCade container")
    try:
        result = subprocess.run(
            [nvidia_smi, "-L"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return NvidiaCapability(False, f"nvidia-smi could not run: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return NvidiaCapability(False, detail or "nvidia-smi did not detect a GPU")
    try:
        ctypes.CDLL("libcuda.so.1")
    except OSError:
        return NvidiaCapability(False, "NVIDIA driver library libcuda.so.1 is not available")
    summary = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "NVIDIA GPU")
    return NvidiaCapability(True, summary)


def configured_gpu_mode() -> str:
    """Return the validated deployment GPU mode."""
    mode = (os.environ.get(GPU_MODE_ENV) or "auto").strip().lower()
    if mode not in {"auto", "cuda", "cpu"}:
        raise ValueError(f"Unknown {GPU_MODE_ENV}={mode!r}; expected auto, cuda, or cpu")
    return mode


@lru_cache(maxsize=32)
def _cached_apptainer_image_cuda_capability(
    resolved: str,
    size: int,
    mtime_ns: int,
) -> NvidiaCapability:
    """Probe one immutable SIF identity, caching the expensive PyTorch startup."""
    del size, mtime_ns
    try:
        result = subprocess.run(
            [
                "apptainer",
                "--quiet",
                "exec",
                "--cleanenv",
                "--no-home",
                "--nv",
                resolved,
                "python3",
                "-c",
                "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return NvidiaCapability(False, f"the tool image CUDA probe could not run: {exc}")
    if result.returncode == 0:
        return NvidiaCapability(True, "CUDA initialized inside the tool image")
    detail = (result.stderr or result.stdout).strip()
    return NvidiaCapability(
        False,
        detail or "the tool image's Python/PyTorch environment cannot initialize CUDA",
    )


def apptainer_image_cuda_capability(image: str) -> NvidiaCapability:
    """Check that a prepared tool image can initialize CUDA through Apptainer."""
    resolved = resolve_apptainer_image(image)
    if resolved.startswith("docker://"):
        return NvidiaCapability(False, "the persistent tool image has not been prepared yet")
    try:
        identity = Path(resolved).stat()
    except OSError as exc:
        return NvidiaCapability(False, f"the prepared tool image cannot be inspected: {exc}")
    return _cached_apptainer_image_cuda_capability(resolved, identity.st_size, identity.st_mtime_ns)


def resolve_gpu_enabled(gpu_preferred: bool, *, image: str | None = None) -> bool:
    """Resolve a GPU-capable workflow to CUDA or CPU for this runtime."""
    if not gpu_preferred:
        return False
    mode = configured_gpu_mode()
    if mode == "cpu":
        return False
    capability = nvidia_capability()
    if not capability.available:
        if mode != "cuda":
            return False
        raise RuntimeGpuUnavailableError(
            "CUDA was requested, but it is unavailable: "
            f"{capability.reason}. Start NeuroCade with Docker GPU passthrough or set {GPU_MODE_ENV}=cpu."
        )
    if image is None:
        return True
    image_capability = apptainer_image_cuda_capability(image)
    if image_capability.available:
        return True
    if mode == "cuda":
        raise RuntimeGpuUnavailableError(
            "CUDA was requested, but the selected tool image cannot use it: "
            f"{image_capability.reason}. Use a CUDA-enabled tool image or set {GPU_MODE_ENV}=cpu."
        )
    return False


def _validate_env(env: dict[str, str]) -> dict[str, str]:
    for key in env:
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ValueError(f"Invalid environment variable name: {key!r}")
    return env


def resolve_apptainer_image(image: str) -> str:
    """Resolve a container image to an Apptainer-runnable reference.

    Prefers a prebuilt, arch-matched SIF from ``NEUROCADE_SIF_DIR`` (the default,
    avoiding a multi-GB pull/convert on every host), and falls back to a
    ``docker://`` URI that Apptainer pulls and caches itself.

    The object-storage download of a missing prebuilt SIF is intentionally not
    performed here; callers that ship prebuilt SIFs place them in the SIF
    directory (named ``<image>-<arch>.sif`` or ``<image>.sif``) ahead of time.
    """
    raw = str(image or "").strip()
    if raw.endswith(".sif") or raw.endswith(".simg"):
        return raw  # already a concrete image file
    name = container_image_name(raw)
    sif_dir = os.environ.get(SIF_DIR_ENV)
    if sif_dir:
        preferred = apptainer_sif_path(name, sif_dir=sif_dir)
        stem = name.replace("/", "_").replace(":", "_")
        for path in (preferred, Path(sif_dir).expanduser() / f"{stem}.sif"):
            if path.is_file():
                return str(path)
    return f"docker://{name}"


def require_network_disabled_image(image: str) -> str:
    """Resolve an image or reject an unprepared Apptainer workflow immediately."""
    resolved = resolve_apptainer_image(image)
    if resolved.startswith("docker://"):
        raise RuntimeError(
            f"Tool image {image!r} is not prepared locally. "
            "Run `./scripts/run.sh prepare-tools` before launching network-disabled workflows."
        )
    return resolved


def assert_rootless_runtime(request: RuntimeContainerRunRequest) -> None:
    """Reject privilege-escalating container options.

    Kept deliberately small (see plan §3.3): the inner tool container must never
    request root/fakeroot or a writable image, even when the surrounding app
    runs in a privileged container.
    """
    for token in request.command:
        text = str(token)
        if text in {"--fakeroot", "--writable", "--writable-tmpfs"} or text.startswith("--fakeroot"):
            raise ValueError(f"Disallowed privilege escalation in tool command: {text}")
    if request.isolated and request.binds:
        raise ValueError("Isolated container execution cannot use bind mounts")
    if request.isolated and request.gpu_enabled:
        raise ValueError("Isolated container execution cannot request a GPU")


def build_container_argv(request: RuntimeContainerRunRequest) -> list[str]:
    """Build an ``apptainer exec`` command for a structured container request."""
    assert_rootless_runtime(request)
    image = require_network_disabled_image(request.image) if request.network_disabled else resolve_apptainer_image(request.image)
    argv: list[str] = ["apptainer", "--quiet", "exec", "--cleanenv", "--no-home"]
    if request.isolated:
        argv.extend(["--contain", "--no-mount", "hostfs,cwd,proc,sys"])
    if request.network_disabled:
        argv.extend(["--net", "--network", "none"])
    if request.gpu_enabled:
        argv.append("--nv")
    for bind in request.binds:
        host_path = _validate_path(str(Path(bind.host_path).expanduser()), label="Bind host path")
        container_path = _validate_path(bind.container_path, label="Container bind path")
        spec = f"{host_path}:{container_path}"
        if bind.mode == "ro":
            spec += ":ro"
        argv.extend(["--bind", spec])
    if request.cwd:
        argv.extend(["--pwd", _validate_path(request.cwd, label="Container working directory")])
    for key, value in sorted(_validate_env(dict(request.env or {})).items()):
        argv.extend(["--env", f"{key}={value}"])
    argv.append(image)
    argv.extend(str(part) for part in request.command)
    return argv
