"""Read-only workspace filesystem helpers used by assistant tools."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.auth import AuthContext
from backend_common.case_storage import case_storage_dir, resolve_case_storage, workspace_storage_dir
from backend_common.db import Case, Workspace, WorkspaceMembership

CONTAINER_WORKSPACE_ROOT = "/workspace"


def workspace_case_rows(db: Session, user_id: str, workspace_id: str) -> list[Case]:
    """Return cases visible to a user in a workspace."""
    rows = (
        db.query(Case, Workspace)
        .join(Workspace, Workspace.id == Case.workspace_id)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Case.workspace_id)
        .filter(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
        .all()
    )
    resolved: list[Case] = []
    for case, workspace in rows:
        try:
            resolve_case_storage(settings, case, workspace)
        except FileNotFoundError:
            continue
        resolved.append(case)
    return sorted(resolved, key=lambda case: case.title)


def selected_workspace_cases(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    case_ids: list[str] | None,
) -> list[Case]:
    """Resolve a requested case selection within the authorized workspace."""
    available = workspace_case_rows(db, context.user.id, workspace.id)
    if not available:
        raise HTTPException(status_code=404, detail="No cases found in this workspace")
    if not case_ids:
        return available
    by_id = {case.id: case for case in available}
    selected: list[Case] = []
    for case_id in case_ids:
        case = by_id.get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail=f"Case not found in workspace: {case_id}")
        selected.append(case)
    return selected


def workspace_case_container_path(case: Case) -> str:
    """Return a case path within the canonical workspace bind."""
    return f"{CONTAINER_WORKSPACE_ROOT}/cases/{case.title}"


def _selected_tree_root(root: Path, relative_path: str) -> tuple[Path, str]:
    relative = str(relative_path or ".").strip().strip("/") or "."
    parts = PurePosixPath(relative).parts
    if ".." in parts:
        raise HTTPException(status_code=400, detail="File-tree path must stay inside the case")
    selected = (root / Path(*parts)).resolve()
    if os.path.commonpath([str(selected), str(root.resolve())]) != str(root.resolve()):
        raise HTTPException(status_code=400, detail="File-tree path must stay inside the case")
    if not selected.is_dir():
        raise HTTPException(status_code=404, detail=f"Case directory not found: {relative_path}")
    return selected, relative


def _append_tree(lines: list[str], root: Path, container_root: str, *, path: str = ".", max_entries: int = 500) -> None:
    selected_root, relative = _selected_tree_root(root, path)
    selected_container_root = container_root if relative == "." else f"{container_root}/{relative}"
    count = 0
    for current_root, dirs, files in os.walk(selected_root):
        dirs.sort()
        files.sort()
        relative_root = Path(current_root).relative_to(selected_root)
        depth = len(relative_root.parts)
        for directory in dirs:
            item = Path(current_root, directory).relative_to(selected_root)
            lines.append(f"{'  ' * depth}{selected_container_root}/{item.as_posix()}/")
            count += 1
            if count >= max_entries:
                lines.append(f"[truncated after {max_entries} entries; inspect a narrower path]")
                return
        for filename in files:
            item = Path(current_root, filename).relative_to(selected_root)
            lines.append(f"{'  ' * depth}{selected_container_root}/{item.as_posix()}")
            count += 1
            if count >= max_entries:
                lines.append(f"[truncated after {max_entries} entries; inspect a narrower path]")
                return


def workspace_case_file_tree(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    case_id: str,
    path: str = ".",
    max_entries: int = 500,
) -> str:
    """Return the complete file tree for one authorized workspace case."""
    case = selected_workspace_cases(db, context, workspace, [case_id])[0]
    case_dir = case_storage_dir(settings, workspace.id, case.id)
    if not case_dir.is_dir():
        raise HTTPException(status_code=404, detail="Case directory not found on disk")
    container_path = workspace_case_container_path(case)
    lines = [
        f"Case `{case.title}` is available at `{container_path}` in workspace workflows.",
        "",
        f"{container_path}/",
    ]
    _append_tree(lines, case_dir, container_path, path=path, max_entries=max_entries)
    return "\n".join(lines)


def workspace_file_tree(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    case_ids: list[str] | None = None,
    path: str = ".",
    max_entries: int = 500,
) -> str:
    """Return the selected case trees as exposed by the workspace bind."""
    selected = selected_workspace_cases(db, context, workspace, case_ids)
    workspace_dir = workspace_storage_dir(settings, workspace.id)
    lines = [
        "Workspace workflows use one writable `/workspace` bind.",
        "",
        "Selected cases:",
    ]
    for case in selected:
        lines.append(f"- {workspace_case_container_path(case)}  # {case.title}")
    lines.extend(["", "File tree preview:"])
    for case in selected:
        container_path = workspace_case_container_path(case)
        case_dir = case_storage_dir(settings, workspace.id, case.id)
        lines.append(f"{container_path}/")
        if case_dir.is_dir():
            _append_tree(lines, case_dir, container_path, path=path, max_entries=max_entries)
    if not workspace_dir.exists():
        lines.extend(["", "The workspace storage directory has not been created yet."])
    return "\n".join(lines)
