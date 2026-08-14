"""Provide API service artifacts behavior for NeuroCade."""

import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from api_service.artifacts.service import (
    resolve_artifact_file_for_user,
    serialize_artifact,
)
from api_service.deps import get_context, get_db
from api_service.helpers import (
    get_case_for_user,
    get_workspace_for_user,
    log_event,
)
from api_service.policies import require_case_read, require_workspace_read
from api_service.runtime import settings
from api_service.runtime_tools.workflow_outputs import index_latest_case_workflow_outputs
from api_service.schemas import ArtifactSummary
from backend_common.artifact_reconciliation import reconcile_artifacts
from backend_common.auth import AuthContext
from backend_common.db import Artifact

router = APIRouter(prefix="/api/app", tags=["artifacts"])


def _write_case_archive(case_dir: Path, archive_path: Path, archive_root: str) -> None:
    """Create a zip archive from regular files under a case directory."""
    case_root = case_dir.resolve()
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        if not case_dir.exists():
            return
        for path in sorted(case_dir.rglob("*")):
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            resolved_path = path.resolve()
            if case_root not in resolved_path.parents:
                continue
            relative_path = path.relative_to(case_dir)
            archive.write(path, arcname=str(Path(archive_root) / relative_path))


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactSummary])
def list_case_artifacts(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[ArtifactSummary]:
    """Return existing artifacts for a readable case."""
    case, _workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    index_latest_case_workflow_outputs(db, settings, case)
    db.commit()
    artifacts = db.query(Artifact).filter(Artifact.case_id == case_id).order_by(Artifact.created_at.desc()).all()
    existing_artifacts = reconcile_artifacts(db, artifacts)
    db.commit()
    return [serialize_artifact(artifact) for artifact in existing_artifacts]


@router.get("/workspaces/{workspace_id}/artifacts", response_model=list[ArtifactSummary])
def list_workspace_artifacts(
    workspace_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[ArtifactSummary]:
    """Return existing top-level artifacts for a readable workspace."""
    _workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_read(role)
    artifacts = (
        db.query(Artifact)
        .filter(Artifact.workspace_id == workspace_id, Artifact.case_id.is_(None))
        .order_by(Artifact.created_at.desc())
        .all()
    )
    existing_artifacts = reconcile_artifacts(db, artifacts)
    db.commit()
    return [serialize_artifact(artifact) for artifact in existing_artifacts]


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> FileResponse:
    """Stream an artifact file after verifying the requesting user can read it."""
    artifact, path = resolve_artifact_file_for_user(db, context, artifact_id)
    # End the authorization read snapshot before the audit write. Concurrent
    # artifact downloads otherwise try to upgrade stale SQLite snapshots.
    db.commit()
    log_event(db, context, "artifact.downloaded", case_id=artifact.case_id, artifact_id=artifact.id)
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.name)


@router.get("/cases/{case_id}/download")
def download_case_archive(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> FileResponse:
    """Build and stream a temporary zip archive for a readable case."""
    case, _workspace, role, case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    # Archive creation can be slow and must not retain a database snapshot;
    # the subsequent audit insert starts in a fresh transaction.
    db.commit()

    with tempfile.NamedTemporaryFile(prefix=f"case-{case.id}-", suffix=".zip", delete=False) as archive_file:
        archive_path = Path(archive_file.name)
    _write_case_archive(case_dir, archive_path, case.title)

    log_event(db, context, "case.downloaded", case_id=case.id, details={"mode": "archive"})
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{case.title}.zip",
        background=BackgroundTask(lambda: archive_path.unlink(missing_ok=True)),
    )
