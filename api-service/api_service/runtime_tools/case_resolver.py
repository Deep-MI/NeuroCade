"""Provide API service case resolver behavior for NeuroCade."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session

from backend_common.case_storage import case_slug_from_id, case_storage_dir, ensure_case_storage_layout
from backend_common.db import Case, Workspace


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
    current_workspace_id = str(state.get("current_workspace_id") or state.get("workspace_id") or "").strip()
    current_case_id = str(state.get("current_case_id") or state.get("case_id") or "").strip()
    if not current_workspace_id or not current_case_id:
        return None
    if any(separator in current_workspace_id or separator in current_case_id for separator in ("/", "\\")):
        return None
    if current_workspace_id in {".", ".."} or current_case_id in {".", ".."}:
        return None

    candidate = Path(output_root) / "workspaces" / current_workspace_id / "cases" / case_slug_from_id(current_workspace_id, current_case_id)
    if candidate.is_symlink():
        return None
    resolved = resolve_host_path_via_existing_parents(candidate)
    if not resolved or not os.path.isdir(resolved):
        return None

    resolved_root = os.path.realpath(str(data_root))
    if os.path.commonpath([resolved, resolved_root]) != resolved_root:
        return None
    return Path(resolved)


def resolve_case_mount_from_db(
    db: Session,
    settings,
    case: Case,
    workspace: Workspace,
) -> Path | None:
    """Resolve the stored case directory for a database-backed workspace case.

    Parameters
    ----------
    db : Session
        Database session used to ensure the case storage layout.
    settings : object
        API settings that define case storage paths.
    case : Case
        Case record to resolve.
    workspace : Workspace
        Workspace that owns the case.

    Returns
    -------
    Path | None
        Existing case directory, or None when it cannot be found.
    """
    case_dir = ensure_case_storage_layout(db, settings, case, workspace)
    if not case_dir.exists():
        case_dir = case_storage_dir(settings, workspace.id, case.id)
    return case_dir if case_dir.exists() else None


def case_container_path_from_local_path(
    local_path: str | Path,
    case_dir: str | Path | None,
    *,
    container_root: str = CONTAINER_CASE_ROOT,
) -> str | None:
    """Map a host path inside a case directory to its container path.

    Parameters
    ----------
    local_path : str | Path
        Host path to translate.
    case_dir : str | Path | None
        Host case directory mounted at ``container_root``.
    container_root : str
        Container mount point for the case directory.

    Returns
    -------
    str | None
        Container path for ``local_path``, or None when it is outside the case.
    """
    if case_dir is None:
        return None

    resolved_case_dir = os.path.realpath(str(case_dir))
    resolved_path = resolve_host_path_via_existing_parents(local_path)
    if not resolved_path:
        return None

    if os.path.commonpath([resolved_path, resolved_case_dir]) != resolved_case_dir:
        return None

    rel = os.path.relpath(resolved_path, resolved_case_dir).replace(os.sep, "/")
    if rel == ".":
        return container_root
    return f"{container_root}/{rel}"
