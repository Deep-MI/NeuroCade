"""Build controlled Apptainer commands for NeuroCade runtime tools."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Mapping, Sequence

from .container_paths import FREESURFER_LICENSE_CONTAINER_PATH, license_path


@dataclass(frozen=True)
class RuntimeBind:
    host_path: Path | str
    container_path: str
    mode: str = "ro"

    def as_argument(self) -> str:
        """Return a validated Apptainer bind argument."""
        if self.mode not in {"ro", "rw"}:
            raise ValueError(f"Unsupported bind mode: {self.mode}")
        container_path = self.container_path.strip()
        if not container_path.startswith("/"):
            raise ValueError("Container bind path must be absolute")
        host_path = Path(self.host_path).expanduser().resolve()
        return f"{host_path}:{container_path}:{self.mode}"


def freesurfer_license_bind_env(
    *,
    root: Path | str | None = None,
    data_root: Path | str | None = None,
) -> tuple[RuntimeBind, dict[str, str]] | None:
    """Return a read-only FreeSurfer license bind plus FS_LICENSE env if available."""
    resolved = license_path(root=Path(root) if root is not None else None, data_root=data_root)
    if resolved is None:
        return None
    return RuntimeBind(resolved, FREESURFER_LICENSE_CONTAINER_PATH, "ro"), {"FS_LICENSE": FREESURFER_LICENSE_CONTAINER_PATH}


def _reject_runtime_option(value: str, label: str) -> str:
    """Reject empty values and option-like runtime arguments."""
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{label} cannot be empty")
    if cleaned.startswith("-"):
        raise ValueError(f"{label} must not look like a runtime option")
    return cleaned


def _reject_no_mount(value: str) -> str:
    """Validate an Apptainer no-mount entry."""
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError("No-mount entry cannot be empty")
    if cleaned.startswith("-") or any(separator in cleaned for separator in " \t\n"):
        raise ValueError(f"Invalid no-mount entry: {value}")
    return cleaned


def apptainer_nv_enabled(mode: str | None = None) -> bool:
    """Return whether Apptainer commands should request NVIDIA support."""
    configured = (mode or os.environ.get("APPTAINER_NV", "auto")).strip().lower()
    return configured in {"1", "true", "yes"} or (configured == "auto" and shutil.which("nvidia-smi") is not None)


def build_apptainer_exec_command(
    *,
    runtime_bin: str = "apptainer",
    image: Path | str,
    command: Sequence[str],
    binds: Sequence[RuntimeBind] = (),
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    disable_network: bool = True,
    no_mounts: Sequence[str] = (),
    cleanenv: bool = True,
    no_home: bool = True,
    nv: bool = False,
    quiet: bool = False,
) -> list[str]:
    """Build a controlled Apptainer/Singularity exec command.

    Callers provide structured binds/env/cwd only. User-supplied raw runtime
    options are intentionally not accepted.
    """
    if not command:
        raise ValueError("Container command cannot be empty")

    runtime = _reject_runtime_option(runtime_bin, "Runtime executable")
    image_path = _reject_runtime_option(str(Path(image).expanduser().resolve()), "Image path")
    result = [runtime]
    if quiet:
        result.append("--quiet")
    result.append("exec")
    if disable_network:
        result.extend(["--net", "--network", "none"])
    for no_mount in no_mounts:
        result.extend(["--no-mount", _reject_no_mount(no_mount)])
    if cleanenv:
        result.append("--cleanenv")
    if no_home:
        result.append("--no-home")
    if nv:
        result.append("--nv")
    for bind in binds:
        result.extend(["--bind", bind.as_argument()])
    if cwd is not None:
        result.extend(["--pwd", str(Path(cwd).expanduser().resolve())])
    if env:
        env_parts = ["env"]
        for key, value in sorted(env.items()):
            if not key.replace("_", "").isalnum() or key[0].isdigit():
                raise ValueError(f"Invalid environment variable name: {key}")
            env_parts.append(f"{key}={value}")
        result.extend([image_path, *env_parts, *command])
    else:
        result.extend([image_path, *command])
    return result
