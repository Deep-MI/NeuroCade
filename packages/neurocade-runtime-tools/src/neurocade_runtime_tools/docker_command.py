"""Build structured Docker runtime requests for NeuroCade tools."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from .container_paths import FREESURFER_LICENSE_CONTAINER_PATH, license_path
from .execution import RuntimeBind, RuntimeContainerRunRequest


def docker_image_name(value: str) -> str:
    """Normalize a Docker image reference from existing NeuroCade specs."""
    image = str(value or "").strip().removeprefix("docker://")
    if not image or image.startswith("-") or any(ch.isspace() for ch in image):
        raise ValueError("Invalid Docker image reference")
    return image


def _validate_docker_path(value: str, *, label: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith("/"):
        raise ValueError(f"{label} must be absolute")
    if "," in cleaned or any(part in cleaned for part in ("\n", "\r", "\t")):
        raise ValueError(f"{label} contains unsupported characters")
    return cleaned


def freesurfer_license_bind_env(
    *,
    root: Path | None = None,
    data_root: str | Path | None = None,
) -> tuple[RuntimeBind, dict[str, str]] | None:
    """Return the FreeSurfer license bind and environment for a container request."""
    resolved = license_path(root=root, data_root=data_root)
    if resolved is None:
        return None
    return RuntimeBind(resolved, FREESURFER_LICENSE_CONTAINER_PATH, "ro"), {"FS_LICENSE": FREESURFER_LICENSE_CONTAINER_PATH}


def build_docker_container_request(
    *,
    image: str,
    command: Sequence[str],
    binds: Sequence[RuntimeBind] = (),
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    disable_network: bool = True,
    gpu: bool = False,
) -> RuntimeContainerRunRequest:
    """Build a structured Docker container execution request.

    Callers provide structured bind/env/cwd values. Runtime-runner owns Docker
    socket access, host path remapping, and final Docker command construction.
    """
    if not command:
        raise ValueError("Container command cannot be empty")
    normalized_binds: list[RuntimeBind] = []
    for bind in binds:
        if bind.mode not in {"ro", "rw"}:
            raise ValueError(f"Unsupported bind mode: {bind.mode}")
        container_path = _validate_docker_path(bind.container_path, label="Container bind path")
        normalized_binds.append(
            RuntimeBind(
                host_path=str(Path(bind.host_path).expanduser().resolve()),
                container_path=container_path,
                mode=bind.mode,
            )
        )
    return RuntimeContainerRunRequest(
        image=docker_image_name(image),
        command=[str(part) for part in command],
        binds=tuple(normalized_binds),
        cwd=_validate_docker_path(cwd, label="Container working directory") if cwd else None,
        env=dict(env or {}),
        network_disabled=disable_network,
        gpu_enabled=gpu,
    )
