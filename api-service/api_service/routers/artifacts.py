"""Provide API service artifacts behavior for NeuroCade."""

import mimetypes
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from neurocade_runtime_tools.bridge_client import BridgeClient, BridgeError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from api_service.artifacts.service import (
    authorize_artifact_for_user,
    resolve_artifact_file_for_user,
    serialize_artifact,
)
from api_service.cases.files import linked_case_file, linked_case_manifest, list_case_files, resolve_case_file
from api_service.deps import get_context, get_db
from api_service.helpers import (
    get_case_for_user,
    get_workspace_for_user,
    log_event,
)
from api_service.policies import require_case_read, require_workspace_read
from api_service.schemas import ArtifactSummary, CaseFileSummary
from backend_common.artifact_reconciliation import existing_artifacts
from backend_common.auth import AuthContext
from backend_common.db import Artifact, LinkedCaseSource

router = APIRouter(prefix="/api/app", tags=["artifacts"])


@router.get("/cases/{case_id}/files", response_model=list[CaseFileSummary])
def list_files(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[CaseFileSummary]:
    """Return the complete case-scoped filesystem view."""
    return list_case_files(db, context, case_id)


@router.get("/cases/{case_id}/files/download", response_model=None)
def download_case_file(
    case_id: str,
    source: Literal["managed", "linked"],
    path: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> Any:
    """Download any case file without requiring an artifact row."""
    resolved = resolve_case_file(db, context, case_id, source, path)
    media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    db.commit()
    log_event(db, context, "case.file_downloaded", case_id=case_id, details={"source": source, "path": path})
    db.commit()
    if resolved.managed_path is not None:
        return FileResponse(resolved.managed_path, media_type=media_type, filename=resolved.name)
    return StreamingResponse(
        resolved.chunks(),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(resolved.name, safe='')}"},
    )


def _write_case_archive(
    case_dir: Path,
    archive_path: Path,
    archive_root: str,
    linked_source: LinkedCaseSource | None = None,
    linked_entries: list[tuple[dict, str]] | None = None,
    bridge: BridgeClient | None = None,
) -> None:
    """Create a zip archive from regular files under a case directory."""
    case_root = case_dir.resolve()
    archived_names: set[str] = set()
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
            archive_name = str(Path(archive_root) / relative_path)
            archive.write(path, arcname=archive_name)
            archived_names.add(archive_name)
        for entry, case_path in linked_entries or []:
            if linked_source is None or bridge is None or entry.get("type") != "file":
                continue
            archive_name = str(Path(archive_root) / Path(str(case_path)))
            if archive_name in archived_names:
                continue
            with archive.open(archive_name, "w") as output:
                resolved = linked_case_file(linked_source, case_path, bridge, int(entry.get("size") or 0))
                for content in resolved.chunks():
                    output.write(content)
            archived_names.add(archive_name)


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactSummary])
def list_case_artifacts(
    case_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[ArtifactSummary]:
    """Return existing artifacts for a readable case."""
    case, _workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    artifacts = db.query(Artifact).filter(Artifact.case_id == case_id).order_by(Artifact.created_at.desc()).all()
    return [serialize_artifact(artifact) for artifact in existing_artifacts(artifacts)]


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
    return [serialize_artifact(artifact) for artifact in existing_artifacts(artifacts)]


@router.get("/artifacts/{artifact_id}/download", response_model=None)
def download_artifact(
    artifact_id: str,
    range_header: str | None = Header(None, alias="Range"),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> Any:
    """Stream an artifact file after verifying the requesting user can read it."""
    artifact = authorize_artifact_for_user(db, context, artifact_id)
    if (artifact.metadata_json or {}).get("source") == "linked" and artifact.case_id:
        resolved = resolve_case_file(db, context, artifact.case_id, "linked", artifact.relative_path)
        size = resolved.size or 0
        start, end, status = 0, size - 1, 200
        if range_header:
            if size == 0:
                raise HTTPException(status_code=416, detail="Byte range is outside the file")
            if not range_header.startswith("bytes=") or "," in range_header:
                raise HTTPException(status_code=416, detail="Only one byte range is supported")
            bounds = range_header.removeprefix("bytes=").split("-", 1)
            try:
                if not bounds[0]:
                    suffix_length = int(bounds[1])
                    if suffix_length <= 0:
                        raise ValueError
                    start, end = max(0, size - suffix_length), size - 1
                else:
                    start = int(bounds[0])
                    end = int(bounds[1]) if bounds[1] else size - 1
            except ValueError as exc:
                raise HTTPException(status_code=416, detail="Invalid byte range") from exc
            if start < 0 or end < start or start >= size:
                raise HTTPException(status_code=416, detail="Byte range is outside the file")
            end = min(end, size - 1)
            status = 206

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(max(0, end - start + 1)),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.name, safe='')}",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        db.commit()
        log_event(db, context, "artifact.downloaded", case_id=artifact.case_id, artifact_id=artifact.id)
        db.commit()
        return StreamingResponse(resolved.chunks(start=start, end=end), status_code=status, media_type=artifact.mime_type, headers=headers)

    artifact, path = resolve_artifact_file_for_user(db, context, artifact_id)
    # End the authorization read snapshot before the audit write. Concurrent
    # artifact downloads otherwise try to upgrade stale SQLite snapshots.
    db.commit()
    log_event(db, context, "artifact.downloaded", case_id=artifact.case_id, artifact_id=artifact.id)
    db.commit()
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
    linked_source = db.query(LinkedCaseSource).filter(LinkedCaseSource.case_id == case.id).one_or_none()
    bridge: BridgeClient | None = None
    linked_entries: list[tuple[dict, str]] = []
    if linked_source is not None:
        bridge, linked_entries = linked_case_manifest(linked_source)
    # Archive creation can be slow and must not retain a database snapshot;
    # the subsequent audit insert starts in a fresh transaction.
    db.commit()

    with tempfile.NamedTemporaryFile(prefix=f"case-{case.id}-", suffix=".zip", delete=False) as archive_file:
        archive_path = Path(archive_file.name)
    try:
        _write_case_archive(case_dir, archive_path, case.title, linked_source, linked_entries, bridge)
    except BridgeError as exc:
        archive_path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=f"Linked source unavailable: {exc}") from exc
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise

    log_event(db, context, "case.downloaded", case_id=case.id, details={"mode": "archive"})
    db.commit()
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{case.title}.zip",
        background=BackgroundTask(lambda: archive_path.unlink(missing_ok=True)),
    )
