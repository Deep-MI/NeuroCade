"""Provide shared backend storage utilities for NeuroCade."""

from pathlib import Path

from backend_common.settings import get_settings


settings = get_settings()


def resolve_artifact_path(relative_path: str) -> Path:
    """Return an artifact path within the configured storage root."""
    candidate = (settings.fs_data_root / relative_path).resolve()
    root = settings.fs_data_root.resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("Artifact path escapes storage root")
    return candidate
