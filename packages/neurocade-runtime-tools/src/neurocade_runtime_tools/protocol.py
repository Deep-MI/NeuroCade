"""Versioned JSON protocol shared by the NeuroCade app and native bridge."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any

PROTOCOL_VERSION = "1"
try:
    BUILD_VERSION = version("neurocade-runtime-tools")
except PackageNotFoundError:  # Source-tree execution before installation.
    BUILD_VERSION = "development"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_CAPTURE_BYTES = 1024 * 1024
TERMINAL_RESULT_TTL_SECONDS = 3600
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RunState(str, Enum):
    accepted = "accepted"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"
    timed_out = "timed_out"


ACTIVE_RUN_STATES = frozenset({RunState.accepted, RunState.running})
TERMINAL_RUN_STATES = frozenset({RunState.completed, RunState.failed, RunState.canceled, RunState.timed_out})


@dataclass(frozen=True, slots=True)
class RuntimeImageSpec:
    """A tagged OCI image and optional immutable Docker/SIF identities."""

    oci_reference: str
    oci_digest: str | None = None
    sif_url: str | None = None
    sif_sha256: str | None = None
    converted_sif_sha256: str | None = None

    def __post_init__(self) -> None:
        value = self.oci_reference.strip()
        image_name = value.rsplit("/", 1)[-1]
        if (
            not value
            or value.startswith("-")
            or ":" not in image_name
            or "@" in value
            or "://" in value
            or any(c.isspace() for c in value)
        ):
            raise ValueError("Runtime images require an explicit tagged OCI reference")
        if self.oci_digest and not _DIGEST.fullmatch(self.oci_digest):
            raise ValueError("OCI digest must be sha256:<64 lowercase hex characters>")
        for label, checksum in (("SIF", self.sif_sha256), ("converted SIF", self.converted_sif_sha256)):
            if checksum and not _SHA256.fullmatch(checksum):
                raise ValueError(f"{label} SHA-256 must contain 64 lowercase hex characters")
        if bool(self.sif_url) != bool(self.sif_sha256):
            raise ValueError("A direct SIF URL and SHA-256 must be supplied together")
        if self.sif_url and not self.sif_url.startswith("https://"):
            raise ValueError("Direct SIF URLs must use HTTPS")

    @property
    def docker_reference(self) -> str:
        return f"{self.oci_reference}@{self.oci_digest}" if self.oci_digest else self.oci_reference

    @property
    def apptainer_reference(self) -> str:
        """Return an immutable OCI reference accepted by Apptainer.

        Docker accepts ``repository:tag@digest`` while Apptainer rejects that
        form. Preserve the human-readable tag in the manifest, but drop it
        when pinning an Apptainer pull to the digest.
        """
        if not self.oci_digest:
            return self.oci_reference
        repository, _tag = self.oci_reference.rsplit(":", 1)
        return f"{repository}@{self.oci_digest}"

    @property
    def image(self) -> str:
        """Compatibility/readability alias for the tagged OCI reference."""
        return self.oci_reference

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RuntimeImageSpec:
        return cls(**value)


def validate_relative_path(value: str, *, label: str, allow_dot: bool = False) -> str:
    """Validate one platform-neutral path beneath the configured data root."""
    raw = str(value or "")
    if "\\" in raw or "\x00" in raw:
        raise ValueError(f"{label} contains unsupported characters")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or (not allow_dot and (not raw or raw == ".")):
        raise ValueError(f"{label} must be a path beneath the host data root")
    return path.as_posix()


def relative_to_data_root(value: str | Path, data_root: str | Path, *, label: str) -> str:
    """Resolve an application path and serialize it relative to HOST_DATA_DIR."""
    root = Path(data_root).expanduser().resolve(strict=True)
    candidate = Path(value).expanduser().resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay under {root}: {candidate}") from exc
    # Existing ancestors are resolved above, preventing symlink escapes. Device
    # files are never valid runtime roots or log destinations.
    existing = candidate if candidate.exists() else next((p for p in candidate.parents if p.exists()), root)
    if existing.exists() and not (existing.is_dir() or existing.is_file()):
        raise ValueError(f"{label} cannot be a device or special file")
    return validate_relative_path(relative.as_posix(), label=label, allow_dot=True)


def validate_environment(env: dict[str, str]) -> dict[str, str]:
    for key, value in env.items():
        if not _ENV_NAME.fullmatch(key):
            raise ValueError(f"Invalid environment variable name: {key!r}")
        if "\x00" in str(value):
            raise ValueError(f"Environment variable {key!r} contains NUL")
    return {str(k): str(v) for k, v in env.items()}


def require_protocol(payload: dict[str, Any]) -> None:
    if str(payload.get("protocol_version")) != PROTOCOL_VERSION:
        raise ValueError(f"Incompatible bridge protocol; expected {PROTOCOL_VERSION}")
