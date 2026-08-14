"""Canonical active-case paths and viewer file classification."""

from __future__ import annotations

from pathlib import Path

from api_service.runtime import settings
from backend_common.case_storage import case_storage_dir

from .case_resolver import resolve_case_mount_from_gui_state

DEFAULT_SEGMENTATION_FILENAME = "aparc.DKTatlas+aseg.deep.mgz"
VOLUME_FILE_SUFFIXES = (".mgz", ".mgh", ".nii", ".nii.gz")
_SEGMENTATION_FILENAME_HINTS = (
    "aseg",
    "aparc",
    "seg",
    "mask",
    "cereb",
    "wmparc",
    "hypothal",
)


def local_output_root() -> Path:
    """Return the configured output directory without caching it at import time."""
    return settings.outputs_dir


def current_case_relative_output_path(gui_state: dict | None) -> str | None:
    """Return the output-root-relative path for the active canonical case."""
    state = gui_state or {}
    workspace_id = str(state.get("workspace_id") or "").strip()
    case_id = str(state.get("case_id") or "").strip()
    if not workspace_id or not case_id:
        return None
    try:
        case_dir = case_storage_dir(settings, workspace_id, case_id)
        prefix = case_dir.relative_to(settings.outputs_dir)
    except (ValueError, FileNotFoundError):
        return None
    return prefix.as_posix()


def looks_like_segmentation(filename: str) -> bool:
    """Return whether a filename appears to reference a segmentation volume."""
    lowered = filename.lower()
    return any(token in lowered for token in _SEGMENTATION_FILENAME_HINTS)


def resolve_case_mount_local_dir(gui_state: dict | None) -> Path | None:
    """Resolve the active case directory from canonical GUI identity fields."""
    return resolve_case_mount_from_gui_state(
        gui_state,
        data_root=settings.fs_data_root,
        output_root=settings.outputs_dir,
    )
