"""Build structured container runtime requests for NeuroCade tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .execution import RuntimeBind, RuntimeContainerRunRequest
from .protocol import RuntimeImageSpec

DCM2NIIX_IMAGE = "vnmd/dcm2niix_v1.0.20240202:20260512"


def container_image_name(value: str) -> str:
    """Normalize a container image reference from existing NeuroCade specs."""
    image = str(value or "").strip()
    if not image or image.startswith("-") or "://" in image or any(ch.isspace() for ch in image):
        raise ValueError("Invalid container image reference")
    return image


def _validate_container_path(value: str, *, label: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith("/"):
        raise ValueError(f"{label} must be absolute")
    if "," in cleaned or any(part in cleaned for part in ("\n", "\r", "\t")):
        raise ValueError(f"{label} contains unsupported characters")
    return cleaned


def build_container_request(
    *,
    image: str | RuntimeImageSpec,
    command: Sequence[str],
    binds: Sequence[RuntimeBind] = (),
    scratch_paths: Sequence[str] = (),
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    disable_network: bool = True,
    gpu: bool = False,
    run_id: str | None = None,
) -> RuntimeContainerRunRequest:
    """Build a structured container execution request.

    Callers provide structured bind/env/cwd values; the bridge's selected runtime
    adapter turns this request into the final ``argv``.
    """
    if not command:
        raise ValueError("Container command cannot be empty")
    normalized_binds: list[RuntimeBind] = []
    for bind in binds:
        if bind.mode not in {"ro", "rw"}:
            raise ValueError(f"Unsupported bind mode: {bind.mode}")
        container_path = _validate_container_path(bind.container_path, label="Container bind path")
        normalized_binds.append(
            RuntimeBind(
                host_path=str(Path(bind.host_path).expanduser().resolve()),
                container_path=container_path,
                mode=bind.mode,
            )
        )
    return RuntimeContainerRunRequest(
        image=image if isinstance(image, RuntimeImageSpec) else RuntimeImageSpec(oci_reference=container_image_name(image)),
        command=[str(part) for part in command],
        binds=tuple(normalized_binds),
        scratch_paths=tuple(
            _validate_container_path(path, label="Scratch path") for path in scratch_paths
        ),
        cwd=_validate_container_path(cwd, label="Container working directory") if cwd else None,
        env=dict(env or {}),
        network_disabled=disable_network,
        gpu_enabled=gpu,
        run_id=run_id,
    )
