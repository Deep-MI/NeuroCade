"""Resolve entity-relative artifact paths against manifest-backed storage."""

from pathlib import Path, PurePosixPath

from backend_common.case_storage import case_storage_dir, workspace_storage_dir
from backend_common.db import Artifact
from backend_common.settings import get_settings

settings = get_settings()


def resolve_artifact_path(artifact: Artifact) -> Path:
    """Return an artifact path confined to its owning case or workspace."""
    relative = PurePosixPath(artifact.relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Artifact path escapes its storage root")
    if artifact.case_id and artifact.workspace_id:
        root = case_storage_dir(settings, artifact.workspace_id, artifact.case_id).resolve()
    elif artifact.workspace_id:
        root = workspace_storage_dir(settings, artifact.workspace_id).resolve()
    else:
        raise ValueError("Artifact has no storage owner")
    candidate = root.joinpath(*relative.parts).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("Artifact path escapes its storage root")
    return candidate
