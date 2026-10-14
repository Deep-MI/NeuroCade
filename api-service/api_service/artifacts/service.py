"""Artifact serialization, lookup, and authorization helpers."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.policies import require_case_read, require_workspace_read
from api_service.schemas import ArtifactSummary
from backend_common.auth import AuthContext
from backend_common.case_storage import (
    case_id_from_storage_dir,
    workspace_id_from_storage_dir,
)
from backend_common.db import Artifact, Case, Workspace
from backend_common.settings import get_settings
from backend_common.storage import resolve_artifact_path

settings = get_settings()


def artifact_download_path(artifact_id: str) -> str:
    """Return the app-relative artifact download path."""
    return f"/artifacts/{artifact_id}/download"


def serialize_artifact(artifact: Artifact) -> ArtifactSummary:
    """Convert an artifact row into its API summary representation."""
    metadata = dict(artifact.metadata_json or {})
    for key in ("linked_source_id", "linked_relative_path", "linked_source_relative"):
        metadata.pop(key, None)
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


def find_artifact_for_relative_path(db: Session, case_id: str, relative_path: str) -> Artifact | None:
    """Find the newest artifact recorded for a storage-relative path."""
    return (
        db.query(Artifact)
        .filter(Artifact.case_id == case_id, Artifact.relative_path == relative_path)
        .order_by(Artifact.created_at.desc())
        .first()
    )


def _case_for_output_relative_path(db: Session, output_relative_path: str) -> tuple[Case | None, Workspace | None]:
    """Resolve a workspace and case from a managed output path."""
    parts = Path(output_relative_path).parts
    if len(parts) < 4 or parts[0] != "workspaces" or parts[2] != "cases":
        return None, None
    workspace_dir = settings.outputs_dir / "workspaces" / parts[1]
    case_dir = workspace_dir / "cases" / parts[3]
    workspace_id = workspace_id_from_storage_dir(workspace_dir)
    case_id = case_id_from_storage_dir(case_dir)
    if not workspace_id or not case_id:
        return None, None
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return None, None
    case = db.get(Case, case_id)
    if case is not None and case.workspace_id != workspace_id:
        case = None
    if case is None:
        return None, None
    return case, workspace


def artifact_for_output(db: Session, context: AuthContext, output_relative_path: str) -> Artifact | None:
    """Resolve a runtime output to its artifact after enforcing case read access."""
    case, workspace = _case_for_output_relative_path(db, output_relative_path)
    if case is not None and workspace is not None:
        _case, _workspace, role, _case_dir = get_case_for_user(db, case.id, context.user.id)
        require_case_read(role)
        relative_path = "/".join(Path(output_relative_path).parts[4:])
    else:
        return None

    artifact = find_artifact_for_relative_path(db, case.id, relative_path)
    if artifact is None:
        return None
    return artifact


def artifact_download_path_for_output(db: Session, context: AuthContext, output_relative_path: str) -> str | None:
    """Return an authorized artifact download path for a runtime output path."""
    artifact = artifact_for_output(db, context, output_relative_path)
    return artifact_download_path(artifact.id) if artifact is not None else None


def resolve_artifact_file_for_user(
    db: Session,
    context: AuthContext,
    artifact_id: str,
) -> tuple[Artifact, str]:
    """Return an authorized artifact and its existing filesystem path."""
    artifact = authorize_artifact_for_user(db, context, artifact_id)
    path = resolve_artifact_path(artifact)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")
    return artifact, str(path)


def authorize_artifact_for_user(db: Session, context: AuthContext, artifact_id: str) -> Artifact:
    """Return an artifact after enforcing its case/workspace read policy."""
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.case_id:
        _case, _workspace, role, _case_dir = get_case_for_user(db, artifact.case_id, context.user.id)
        require_case_read(role)
    elif artifact.workspace_id:
        _workspace, role = get_workspace_for_user(db, artifact.workspace_id, context.user.id)
        require_workspace_read(role)
    else:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact
