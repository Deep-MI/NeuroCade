"""Artifact serialization, lookup, and authorization helpers."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.policies import require_case_read, require_workspace_read
from api_service.schemas import ArtifactSummary
from backend_common.auth import AuthContext
from backend_common.case_storage import build_case_id, case_storage_dir, workspace_storage_dir
from backend_common.db import Artifact, ArtifactKind, Case, Workspace
from backend_common.scan import classify_volume_metadata
from backend_common.settings import get_settings
from backend_common.storage import resolve_artifact_path

settings = get_settings()

def artifact_download_path(artifact_id: str) -> str:
    """Return the app-relative artifact download path."""
    return f"/artifacts/{artifact_id}/download"


def serialize_artifact(artifact: Artifact) -> ArtifactSummary:
    """Convert an artifact row into its API summary representation."""
    metadata = dict(artifact.metadata_json or {})
    if artifact.kind == ArtifactKind.volume:
        inferred_metadata = classify_volume_metadata(artifact.name)
        if "volume_role" not in metadata or inferred_metadata.get("volume_role") == "segmentation":
            metadata.update(inferred_metadata)
    return ArtifactSummary(
        id=artifact.id,
        case_id=artifact.case_id,
        workspace_id=artifact.workspace_id,
        kind=artifact.kind.value,
        name=artifact.name,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
        download_path=artifact_download_path(artifact.id),
        metadata=metadata,
    )


def artifact_exists_on_disk(artifact: Artifact) -> bool:
    """Return whether the artifact's stored relative path resolves to a file."""
    try:
        path = resolve_artifact_path(artifact.relative_path)
    except ValueError:
        return False
    if artifact.case_id and artifact.workspace_id:
        expected_root = case_storage_dir(settings, artifact.workspace_id, artifact.case_id).resolve()
        if expected_root not in path.parents and path != expected_root:
            return False
    elif artifact.workspace_id:
        expected_root = workspace_storage_dir(settings, artifact.workspace_id).resolve()
        if expected_root not in path.parents and path != expected_root:
            return False
    else:
        return False
    return path.exists()


def filter_existing_artifacts(artifacts: list[Artifact]) -> list[Artifact]:
    """Keep only artifacts whose files are still present on disk."""
    return [artifact for artifact in artifacts if artifact_exists_on_disk(artifact)]


def find_artifact_for_relative_path(db: Session, relative_path: str) -> Artifact | None:
    """Find the newest artifact recorded for a storage-relative path."""
    return (
        db.query(Artifact)
        .filter(Artifact.relative_path == relative_path)
        .order_by(Artifact.created_at.desc())
        .first()
    )


def _case_for_output_relative_path(db: Session, output_relative_path: str) -> tuple[Case | None, Workspace | None]:
    """Resolve a workspace and case from a managed output path."""
    normalized = str(output_relative_path or "").strip().lstrip("/")
    if normalized.startswith("output/"):
        normalized = normalized.removeprefix("output/")
    parts = Path(normalized).parts
    if len(parts) < 2:
        return None, None

    if len(parts) < 4 or parts[0] != "workspaces" or parts[2] != "cases":
        return None, None
    workspace_id = parts[1].strip()
    case_slug = parts[3].strip()
    if not workspace_id:
        return None, None

    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.status != "active":
        return None, None
    try:
        case_id = build_case_id(workspace_id, case_slug)
    except ValueError:
        return None, None
    case = db.get(Case, case_id)
    if case is not None and case.workspace_id != workspace_id:
        case = None
    if case is None:
        return None, None
    return case, workspace


def artifact_download_path_for_output(db: Session, context: AuthContext, output_relative_path: str) -> str | None:
    """Return an authorized artifact download path for a runtime output path."""
    normalized = str(output_relative_path or "").strip().lstrip("/")
    if normalized.startswith("output/"):
        normalized = normalized.removeprefix("output/")

    case, workspace = _case_for_output_relative_path(db, normalized)
    if case is not None and workspace is not None:
        _case, role = get_case_for_user(db, case.id, context.user.id)
        require_case_read(role)
        relative_path = f"output/{normalized}"
    else:
        return None

    artifact = find_artifact_for_relative_path(db, relative_path)
    if artifact is None:
        return None
    return artifact_download_path(artifact.id)


def resolve_artifact_file_for_user(
    db: Session,
    context: AuthContext,
    artifact_id: str,
) -> tuple[Artifact, str]:
    """Return an authorized artifact and its existing filesystem path."""
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.case_id:
        case, role = get_case_for_user(db, artifact.case_id, context.user.id)
        require_case_read(role)
        expected_root = case_storage_dir(settings, case.workspace_id, case.id).resolve()
    elif artifact.workspace_id:
        workspace, role = get_workspace_for_user(db, artifact.workspace_id, context.user.id)
        require_workspace_read(role)
        expected_root = workspace_storage_dir(settings, workspace.id).resolve()
    else:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = resolve_artifact_path(artifact.relative_path)
    if expected_root not in path.parents and path != expected_root:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")
    return artifact, str(path)
