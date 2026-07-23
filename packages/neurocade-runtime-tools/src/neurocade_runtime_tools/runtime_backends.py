"""Pluggable in-process runtime backends.

The monolith launches analysis tools (FastSurfer, dcm2niix, the bash image) by
turning a backend-agnostic :class:`RuntimeContainerRunRequest` into a concrete
``argv`` that is run as a local subprocess. This replaces the former
``runtime-runner`` HTTP sidecar, which existed only because containerised
services could not reach the Docker socket.

Two backends are provided:

* :class:`ApptainerBackend` -- the default/production runtime. ``apptainer exec``
  needs no daemon or socket and runs in both the native and (privileged)
  container app deployments. Images are resolved to a prebuilt, arch-matched SIF
  when one is available, otherwise an ``docker://`` URI is handed to Apptainer to
  pull/convert on the fly.
* :class:`DockerBackend` -- a native-only developer convenience selected with
  ``NEUROCADE_RUNTIME_BACKEND=docker``. Useful on hosts that have Docker but not
  Apptainer (e.g. local macOS development).

The backend is chosen once per process from ``NEUROCADE_RUNTIME_BACKEND``
(defaulting to ``apptainer``).
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Protocol

from .container_request import _validate_container_path as _validate_path
from .container_request import container_image_name
from .execution import RuntimeContainerRunRequest

RUNTIME_BACKEND_ENV = "NEUROCADE_RUNTIME_BACKEND"
SIF_DIR_ENV = "NEUROCADE_SIF_DIR"


def _normalise_arch() -> str:
    """Return the host CPU architecture as ``amd64`` or ``arm64``."""
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


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
        stem = name.replace("/", "_").replace(":", "_")
        arch = _normalise_arch()
        for candidate in (f"{stem}-{arch}.sif", f"{stem}.sif"):
            path = Path(sif_dir).expanduser() / candidate
            if path.is_file():
                return str(path)
    return f"docker://{name}"


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


class RuntimeBackend(Protocol):
    """Turn a structured container request into a concrete subprocess ``argv``."""

    name: str

    def build_argv(self, request: RuntimeContainerRunRequest) -> list[str]:
        """Return the executable ``argv`` for the container request."""
        ...


class DockerBackend:
    """Run a tool container with ``docker run`` (native-only dev convenience)."""

    name = "docker"

    def build_argv(self, request: RuntimeContainerRunRequest) -> list[str]:
        assert_rootless_runtime(request)
        argv: list[str] = ["docker", "run"]
        if request.remove:
            argv.append("--rm")
        if request.network_disabled:
            argv.extend(["--network", "none"])
        if request.gpu_enabled:
            argv.extend(["--gpus", "all"])
        for key, value in sorted(_validate_env(dict(request.env or {})).items()):
            argv.extend(["--env", f"{key}={value}"])
        for bind in request.binds:
            host_path = _validate_path(str(Path(bind.host_path).expanduser()), label="Bind host path")
            container_path = _validate_path(bind.container_path, label="Container bind path")
            mount = f"type=bind,src={host_path},dst={container_path}"
            if bind.mode == "ro":
                mount += ",readonly"
            argv.extend(["--mount", mount])
        if request.cwd:
            argv.extend(["--workdir", _validate_path(request.cwd, label="Container working directory")])
        argv.append(container_image_name(request.image))
        argv.extend(str(part) for part in request.command)
        return argv


class ApptainerBackend:
    """Run a tool container with ``apptainer exec`` (default/production)."""

    name = "apptainer"

    def build_argv(self, request: RuntimeContainerRunRequest) -> list[str]:
        assert_rootless_runtime(request)
        argv: list[str] = ["apptainer", "exec", "--cleanenv", "--no-home"]
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
        argv.append(resolve_apptainer_image(request.image))
        argv.extend(str(part) for part in request.command)
        return argv


_BACKENDS: dict[str, RuntimeBackend] = {
    "apptainer": ApptainerBackend(),
    "docker": DockerBackend(),
}


def select_runtime_backend() -> RuntimeBackend:
    """Return the runtime backend configured for this process."""
    requested = (os.environ.get(RUNTIME_BACKEND_ENV) or "apptainer").strip().lower()
    try:
        return _BACKENDS[requested]
    except KeyError as exc:
        raise ValueError(
            f"Unknown {RUNTIME_BACKEND_ENV}={requested!r}; expected one of {sorted(_BACKENDS)}"
        ) from exc


def build_container_argv(request: RuntimeContainerRunRequest) -> list[str]:
    """Build the ``argv`` for a container request using the selected backend."""
    return select_runtime_backend().build_argv(request)
