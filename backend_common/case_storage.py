"""Manifest-backed workspace and case storage."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from pathlib import Path

from sqlalchemy.orm.attributes import set_committed_value

from backend_common.db import Case, Workspace

UPLOAD_SUFFIXES = (".nii.gz", ".nii", ".mgz")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
WORKSPACE_MANIFEST = ".neurocade-workspace.json"
CASE_MANIFEST = ".neurocade-case.json"


def slugify_storage_name(value: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return candidate.strip("-")[:64].rstrip("-")


def _validate_storage_name(value: str, label: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError(f"{label} cannot be empty")
    if not SLUG_PATTERN.fullmatch(candidate):
        raise ValueError(f"{label} must be a lowercase slug, 2-64 characters, using only a-z, 0-9, and hyphen")
    return candidate


def validate_workspace_name(name: str) -> str:
    return _validate_storage_name(name, "Workspace name")


def validate_case_title(title: str) -> str:
    return _validate_storage_name(title, "Case name")


def upload_extension(filename: str) -> str:
    return ".nii.gz" if filename.lower().endswith(".nii.gz") else Path(filename).suffix


def case_title_from_filename(filename: str) -> str:
    stem = filename[:-7] if filename.lower().endswith(".nii.gz") else Path(filename).stem
    return slugify_storage_name(stem)


def _read_manifest(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    identifier = payload.get("id") if isinstance(payload, dict) else None
    return identifier if isinstance(identifier, str) and identifier else None


def _write_manifest(path: Path, identifier: str) -> None:
    path.write_text(json.dumps({"id": identifier}, indent=2) + "\n", encoding="utf-8")


def workspace_id_from_storage_dir(path: Path) -> str | None:
    return _read_manifest(path / WORKSPACE_MANIFEST)


def case_id_from_storage_dir(path: Path) -> str | None:
    return _read_manifest(path / CASE_MANIFEST)


def _find_manifest_dir(parent: Path, manifest_name: str, identifier: str) -> Path:
    matches = [
        entry
        for entry in parent.iterdir()
        if entry.is_dir() and not entry.is_symlink() and _read_manifest(entry / manifest_name) == identifier
    ] if parent.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise RuntimeError(f"Duplicate storage manifests for {identifier}")
    raise FileNotFoundError(f"Storage manifest not found for {identifier}")


def workspace_storage_dir_from_root(output_root: Path, workspace_id: str) -> Path:
    return _find_manifest_dir(output_root / "workspaces", WORKSPACE_MANIFEST, workspace_id)


def workspace_storage_dir(settings, workspace_id: str) -> Path:
    """Resolve a workspace directory by its immutable manifest ID."""
    return workspace_storage_dir_from_root(settings.outputs_dir, workspace_id)


def case_storage_dir_from_root(output_root: Path, workspace_id: str, case_id: str) -> Path:
    workspace_dir = workspace_storage_dir_from_root(output_root, workspace_id)
    return _find_manifest_dir(workspace_dir / "cases", CASE_MANIFEST, case_id)


def case_storage_dir(settings, workspace_id: str, case_id: str) -> Path:
    """Resolve a case directory by its immutable manifest ID."""
    return case_storage_dir_from_root(settings.outputs_dir, workspace_id, case_id)


def resolve_workspace_storage(settings, workspace: Workspace) -> Path:
    """Resolve workspace storage and project its authoritative directory name."""
    directory = workspace_storage_dir(settings, workspace.id)
    set_committed_value(workspace, "name", directory.name)
    return directory


def resolve_case_storage(settings, case: Case, workspace: Workspace) -> Path:
    """Resolve case storage and project authoritative filesystem names."""
    resolve_workspace_storage(settings, workspace)
    directory = case_storage_dir(settings, workspace.id, case.id)
    set_committed_value(case, "title", directory.name)
    return directory


def ensure_workspace_storage_layout(settings, workspace: Workspace) -> Path:
    """Create or resolve a workspace directory and identity manifest."""
    try:
        path = workspace_storage_dir(settings, workspace.id)
    except FileNotFoundError:
        path = settings.outputs_dir / "workspaces" / validate_workspace_name(workspace.name)
        if path.exists():
            raise FileExistsError(f"Workspace storage already exists: {path}") from None
        path.mkdir(parents=True)
        _write_manifest(path / WORKSPACE_MANIFEST, workspace.id)
    (path / "cases").mkdir(exist_ok=True)
    return path


def ensure_case_storage_layout(settings, case: Case, workspace: Workspace) -> Path:
    """Create or resolve a case directory and identity manifest."""
    workspace_dir = ensure_workspace_storage_layout(settings, workspace)
    try:
        path = case_storage_dir(settings, workspace.id, case.id)
    except FileNotFoundError:
        path = workspace_dir / "cases" / validate_case_title(case.title)
        if path.exists():
            raise FileExistsError(f"Case storage already exists: {path}") from None
        path.mkdir(parents=True)
        _write_manifest(path / CASE_MANIFEST, case.id)

    return path


def rename_case_storage(settings, workspace_id: str, case_id: str, new_title: str) -> Path:
    source = case_storage_dir(settings, workspace_id, case_id)
    target = source.with_name(validate_case_title(new_title))
    if target != source:
        if target.exists():
            raise FileExistsError(f"Case storage already exists: {target}")
        source.replace(target)
    return target


def rename_workspace_storage(settings, workspace_id: str, new_name: str) -> Path:
    source = workspace_storage_dir(settings, workspace_id)
    target = source.with_name(validate_workspace_name(new_name))
    if target != source:
        if target.exists():
            raise FileExistsError(f"Workspace storage already exists: {target}")
        source.replace(target)
    return target


def unique_upload_name(case_dir: Path, source_name: str) -> str:
    base = Path(source_name).name
    stem, ext = case_title_from_filename(base), upload_extension(base)
    candidate = f"{stem}{ext}"
    index = 2
    while (case_dir / candidate).exists():
        candidate = f"{stem}-{index}{ext}"
        index += 1
    return candidate


def case_named_upload(case_title: str, source_name: str) -> str:
    return f"{case_title}{upload_extension(source_name)}"


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        for child in path.iterdir():
            _remove_path(child)
        path.rmdir()


def delete_case_storage(settings, case: Case, workspace: Workspace) -> None:
    with suppress(FileNotFoundError):
        _remove_path(case_storage_dir(settings, workspace.id, case.id))
