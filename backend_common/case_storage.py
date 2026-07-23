"""Provide shared backend case storage utilities for NeuroCade."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from backend_common.db import Artifact, Case, Workspace

UPLOAD_SUFFIXES = (".nii.gz", ".nii", ".mgz")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
CASE_ID_SEPARATOR = "__"


def slugify_storage_name(value: str) -> str:
    """Return a best-effort slug storage name from free text."""
    candidate = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    candidate = candidate.strip("-")[:64].rstrip("-")
    return candidate


def _validate_storage_name(value: str, label: str) -> str:
    """Return a slug-safe storage name or raise a labeled validation error."""
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError(f"{label} cannot be empty")
    if not SLUG_PATTERN.fullmatch(candidate):
        raise ValueError(
            f"{label} must be a lowercase slug, 2-64 characters, using only a-z, 0-9, and hyphen"
        )
    return candidate


def validate_workspace_name(name: str) -> str:
    """Validate and normalize a workspace name for storage."""
    return _validate_storage_name(name, "Workspace name")


def validate_case_title(title: str) -> str:
    """Validate and normalize a case title for storage."""
    return _validate_storage_name(title, "Case name")


def upload_extension(filename: str) -> str:
    """Return the upload extension, preserving compound NIfTI suffixes."""
    lower = filename.lower()
    if lower.endswith(".nii.gz"):
        return ".nii.gz"
    return Path(filename).suffix


def case_title_from_filename(filename: str) -> str:
    """Return the default case title derived from an upload filename."""
    lower = filename.lower()
    if lower.endswith(".nii.gz"):
        return slugify_storage_name(filename[:-7])
    return slugify_storage_name(Path(filename).stem)


def build_case_id(workspace_id: str, case_slug: str) -> str:
    """Return the global DB case id for a workspace-local case slug."""
    return f"{_validate_storage_name(workspace_id, 'Workspace name')}{CASE_ID_SEPARATOR}{_validate_storage_name(case_slug, 'Case name')}"


def case_slug_from_id(workspace_id: str, case_id: str) -> str:
    """Return the workspace-local case slug from a global case id."""
    workspace_slug = _validate_storage_id(workspace_id, "Workspace id")
    case_storage_id = _validate_storage_id(case_id, "Case id")
    prefix = f"{workspace_slug}{CASE_ID_SEPARATOR}"
    if not case_storage_id.startswith(prefix):
        raise ValueError(f"Case id must use the canonical '<workspace>{CASE_ID_SEPARATOR}<case>' format")
    return _validate_storage_name(case_storage_id[len(prefix) :], "Case slug")


def _validate_storage_id(value: str, label: str) -> str:
    """Return a path-safe immutable ID or raise a labeled validation error."""
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError(f"{label} cannot be empty")
    if candidate in {".", ".."}:
        raise ValueError(f"{label} is invalid")
    if "/" in candidate or "\\" in candidate:
        raise ValueError(f"{label} must not contain path separators")
    return candidate


def workspace_storage_relative_prefix(workspace_id: str) -> str:
    """Return the artifact prefix for workspace-scoped output."""
    return f"output/workspaces/{_validate_storage_id(workspace_id, 'Workspace id')}"


def workspace_storage_dir(settings, workspace_id: str) -> Path:
    """Return the absolute storage directory for a workspace."""
    return settings.outputs_dir / "workspaces" / _validate_storage_id(workspace_id, "Workspace id")


def case_relative_prefix(workspace_id: str, case_id: str) -> str:
    """Return the artifact prefix for case-scoped output."""
    return f"{workspace_storage_relative_prefix(workspace_id)}/cases/{case_slug_from_id(workspace_id, case_id)}"


def case_storage_dir(settings, workspace_id: str, case_id: str) -> Path:
    """Return the absolute storage directory for a case."""
    return workspace_storage_dir(settings, workspace_id) / "cases" / case_slug_from_id(workspace_id, case_id)


def workspace_analysis_relative_prefix(workspace_id: str, analysis_id: str) -> str:
    """Return the artifact prefix for a workspace analysis."""
    return f"{workspace_storage_relative_prefix(workspace_id)}/workspace-analyses/{_validate_storage_id(analysis_id, 'Analysis id')}"


def workspace_analysis_dir(settings, workspace_id: str, analysis_id: str) -> Path:
    """Return the absolute storage directory for a workspace analysis."""
    return workspace_storage_dir(settings, workspace_id) / "workspace-analyses" / _validate_storage_id(analysis_id, "Analysis id")


def list_case_upload_files(settings, workspace: Workspace, case: Case) -> list[Path]:
    """Return sorted uploaded image files stored for a case."""
    case_dir = case_storage_dir(settings, workspace.id, case.id)
    if not case_dir.exists():
        return []
    uploads: list[Path] = []
    for entry in case_dir.iterdir():
        if entry.is_symlink() or not entry.is_file():
            continue
        if upload_extension(entry.name) not in UPLOAD_SUFFIXES:
            continue
        uploads.append(entry)
    return sorted(uploads)


def unique_upload_name(case_dir: Path, source_name: str) -> str:
    """Return a non-conflicting upload filename for a case directory."""
    base = Path(source_name).name
    stem = case_title_from_filename(base)
    ext = upload_extension(base)
    candidate = f"{stem}{ext}"
    index = 2
    while (case_dir / candidate).exists():
        candidate = f"{stem}-{index}{ext}"
        index += 1
    return candidate


def canonical_upload_name(case_title: str, source_name: str) -> str:
    """Return the canonical upload filename for a case title."""
    return f"{case_title}{upload_extension(source_name)}"


def case_resource_relative_path(workspace: Workspace, case: Case, resource_suffix: str) -> str:
    """Return a case artifact path with the resource suffix appended."""
    clean_suffix = resource_suffix.lstrip("/")
    return f"{case_relative_prefix(workspace.id, case.id)}/{clean_suffix}"


def _remove_path(path: Path) -> None:
    """Remove a file or directory tree if it exists."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        for child in path.iterdir():
            _remove_path(child)
        path.rmdir()


def _prune_empty_workspace_dirs(settings, workspace_id: str) -> None:
    """Remove the workspace storage directory when it is empty."""
    workspace_dir = workspace_storage_dir(settings, workspace_id)
    if workspace_dir.exists() and workspace_dir.is_dir() and not any(workspace_dir.iterdir()):
        workspace_dir.rmdir()


def ensure_case_storage_layout(
    db: Session,
    settings,
    case: Case,
    workspace: Workspace,
    *,
    preferred_upload_name: str | None = None,
) -> Path:
    """Create the canonical case storage layout."""
    validate_workspace_name(workspace.name)
    validate_case_title(case.title)

    new_case_dir = case_storage_dir(settings, workspace.id, case.id)
    new_case_dir.mkdir(parents=True, exist_ok=True)

    _prune_empty_workspace_dirs(settings, workspace.id)

    artifacts = (
        db.query(Artifact)
        .filter(Artifact.case_id == case.id)
        .order_by(Artifact.created_at.asc(), Artifact.id.asc())
        .all()
    )
    for artifact in artifacts:
        artifact.workspace_id = workspace.id
        disk_path = settings.fs_data_root / artifact.relative_path
        if disk_path.exists():
            artifact.size_bytes = disk_path.stat().st_size

    return new_case_dir


def ensure_workspace_analysis_storage_layout(settings, workspace_id: str, analysis_id: str) -> Path:
    """Create and return the storage directory for a workspace analysis."""
    normalized_analysis_id = (analysis_id or "").strip()
    if not normalized_analysis_id:
        raise ValueError("Analysis id cannot be empty")

    new_analysis_dir = workspace_analysis_dir(settings, workspace_id, normalized_analysis_id)
    new_analysis_dir.mkdir(parents=True, exist_ok=True)

    _prune_empty_workspace_dirs(settings, workspace_id)

    return new_analysis_dir


def delete_case_storage(settings, case: Case, workspace: Workspace) -> None:
    """Delete canonical case storage."""
    _remove_path(case_storage_dir(settings, workspace.id, case.id))
    _prune_empty_workspace_dirs(settings, workspace.id)


def delete_workspace_storage(settings, workspace: Workspace) -> None:
    """Delete canonical workspace storage."""
    _remove_path(workspace_storage_dir(settings, workspace.id))
