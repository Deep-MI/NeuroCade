"""Filesystem helpers for workspace batch command mounts and file listings."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.workspace_batch.queries import select_cases_for_batch, selected_cases_for_run
from backend_common.auth import AuthContext
from backend_common.case_storage import case_slug_from_id, case_storage_dir, ensure_workspace_analysis_storage_layout
from backend_common.db import Case, Run, Workspace


def workspace_input_root(analysis_id: str) -> Path:
    """Return the host directory that stores generated inputs for an analysis."""
    return settings.fs_data_root / ".workspace-inputs" / analysis_id


def workspace_cases_mount_dir(analysis_id: str) -> Path:
    """Return the host directory containing per-case bind metadata for an analysis."""
    return workspace_input_root(analysis_id) / "cases"


def workspace_case_mount_name(case: Case) -> str:
    """Return the stable mount directory name for a workspace case."""
    return case_slug_from_id(case.workspace_id, case.id)


def workspace_case_mount_path(case: Case) -> str:
    """Return the container-visible case mount path."""
    return f"/cases/{workspace_case_mount_name(case)}"


def runtime_visible_data_path(path: Path) -> str:
    """Return a canonical host path below the configured data root for runtime dispatch."""
    resolved_root = settings.fs_data_root.resolve()
    resolved_path = path.resolve()
    resolved_path.relative_to(resolved_root)
    return str(resolved_path)


def prepare_workspace_command_inputs(
    db: Session,
    parent_run: Run,
    workspace: Workspace,
    *,
    analysis_id: str,
) -> tuple[Path, Path]:
    """Create case bind metadata and a manifest for a workspace batch analysis."""
    analysis_dir = ensure_workspace_analysis_storage_layout(settings, workspace.id, analysis_id)

    cases_dir = workspace_cases_mount_dir(analysis_id)
    cases_dir.mkdir(parents=True, exist_ok=True)
    for entry in cases_dir.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    selected_cases = selected_cases_for_run(db, parent_run)
    case_entries: list[dict[str, str]] = []
    bind_entries: list[dict[str, str]] = []
    for case in selected_cases:
        case_dir = case_storage_dir(settings, workspace.id, case.id).resolve()
        case_entries.append(
            {
                "case_id": case.id,
                "case_title": case.title,
                "mount_path": workspace_case_mount_path(case),
            }
        )
        bind_entries.append(
            {
                "case_id": case.id,
                "case_title": case.title,
                "mount_name": workspace_case_mount_name(case),
                "mount_path": workspace_case_mount_path(case),
                "host_path": str(case_dir),
            }
        )

    cases_manifest_path = analysis_dir / "cases.json"
    cases_manifest_path.write_text(json.dumps(case_entries, indent=2) + "\n", encoding="utf-8")
    (cases_dir / "cases.json").write_text(json.dumps(bind_entries, indent=2) + "\n", encoding="utf-8")
    return analysis_dir, cases_dir


def workspace_case_file_tree(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    case_id: str,
) -> str:
    """Return a readable file tree for one selected case mounted at /case."""
    selected_cases = select_cases_for_batch(db, context, workspace, [case_id], require_idle=False)
    target_case = selected_cases[0]
    case_dir = case_storage_dir(settings, workspace.id, target_case.id)
    if not case_dir.exists():
        raise HTTPException(status_code=404, detail="Case directory not found on disk")

    lines = [
        f"Case `{target_case.title}` is mounted at `/case` for workspace batch commands.",
        "",
        "/case/",
    ]
    for root, dirs, files in os.walk(case_dir):
        dirs.sort()
        files.sort()
        rel_root = Path(root).relative_to(case_dir)
        depth = len(rel_root.parts)
        for directory in dirs:
            indent = "  " * depth
            directory_rel = Path(root, directory).relative_to(case_dir)
            lines.append(f"{indent}/case/{directory_rel.as_posix()}/")
        for filename in files:
            indent = "  " * depth
            file_rel = Path(root, filename).relative_to(case_dir)
            lines.append(f"{indent}/case/{file_rel.as_posix()}")
    return "\n".join(lines)


def workspace_file_tree(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    case_ids: list[str] | None = None,
) -> str:
    """Return mount paths and file trees for selected workspace cases."""
    selected_cases = select_cases_for_batch(db, context, workspace, case_ids, require_idle=False)
    lines = [
        "Workspace-scoped commands mount the selected cases read-only under /cases/<case-slug>/",
        "and write generated outputs into the dedicated writable /workspace/ analysis folder.",
        "",
        "Selected case mounts:",
    ]
    for case in selected_cases:
        lines.append(f"- {workspace_case_mount_path(case)}  # {case.title}")
    lines.extend(["", "Writable output root:", "/workspace/", "", "File tree preview:"])

    for case in selected_cases:
        lines.append(f"{workspace_case_mount_path(case)}/")
        case_dir = case_storage_dir(settings, workspace.id, case.id)
        for root, dirs, files in os.walk(case_dir):
            dirs.sort()
            files.sort()
            rel_root = Path(root).relative_to(case_dir)
            depth = len(rel_root.parts)
            for directory in dirs:
                directory_rel = Path(root, directory).relative_to(case_dir)
                lines.append(f"{'  ' * (depth + 1)}{workspace_case_mount_path(case)}/{directory_rel.as_posix()}/")
            for filename in files:
                file_rel = Path(root, filename).relative_to(case_dir)
                lines.append(f"{'  ' * (depth + 1)}{workspace_case_mount_path(case)}/{file_rel.as_posix()}")
    return "\n".join(lines)
