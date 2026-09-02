"""Provide API service case resolver behavior for NeuroCade."""

from __future__ import annotations

import os
from pathlib import Path

from backend_common.case_storage import case_storage_dir_from_root

CONTAINER_CASE_ROOT = "/case"


def resolve_host_path_via_existing_parents(host_path: str | Path) -> str | None:
    """Resolve a path by following symlinks through its existing parent chain.

    Parameters
    ----------
    host_path : str | Path
        Host path that may include missing trailing components.

    Returns
    -------
    str | None
        Real path with missing suffix components restored, or None if no parent exists.
    """
    probe = os.path.abspath(str(host_path))
    suffix_parts: list[str] = []

    while not os.path.lexists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        suffix_parts.append(os.path.basename(probe))
        probe = parent

    resolved = os.path.realpath(probe)
    for part in reversed(suffix_parts):
        resolved = os.path.join(resolved, part)
    return resolved


def resolve_case_mount_from_gui_state(
    gui_state: dict | None,
    *,
    data_root: str | Path,
    output_root: str | Path,
) -> Path | None:
    """Resolve the active GUI case output directory under the configured data root.

    Parameters
    ----------
    gui_state : dict | None
        Browser state that may include canonical workspace and case IDs.
    data_root : str | Path
        Host data root that must contain the resolved case directory.
    output_root : str | Path
        Base output path used to interpret the GUI case path.

    Returns
    -------
    Path | None
        Existing case directory when it is inside ``data_root``; otherwise None.
    """
    state = gui_state or {}
    workspace_id = str(state.get("workspace_id") or "").strip()
    case_id = str(state.get("case_id") or "").strip()
    if not workspace_id or not case_id:
        return None
    if any(separator in workspace_id or separator in case_id for separator in ("/", "\\")):
        return None
    if workspace_id in {".", ".."} or case_id in {".", ".."}:
        return None

    try:
        candidate = case_storage_dir_from_root(Path(output_root), workspace_id, case_id)
    except FileNotFoundError:
        return None
    if candidate.is_symlink():
        return None
    resolved = resolve_host_path_via_existing_parents(candidate)
    if not resolved or not os.path.isdir(resolved):
        return None

    resolved_root = os.path.realpath(str(data_root))
    if os.path.commonpath([resolved, resolved_root]) != resolved_root:
        return None
    return Path(resolved)
