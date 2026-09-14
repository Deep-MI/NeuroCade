"""Authenticated binary transfers, scoped to the paired workspace."""

import hashlib
import shutil
import tempfile
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from api_service.artifacts.service import resolve_artifact_file_for_user
from api_service.cases.operations import add_upload_to_case, create_case_from_upload
from api_service.cases.service import ensure_case_not_active, validate_case_name_or_400
from api_service.deps import get_db
from api_service.helpers import get_case_for_user, log_event
from api_service.mcp_adapter.auth import authenticate, resolve_client, validate_local_headers
from api_service.policies import require_case_read
from backend_common.case_storage import case_title_from_filename
from backend_common.db import Artifact, AssistantToolExecution
from backend_common.output_activity import OutputBusy, ensure_outputs_idle, reserve_case_files
from backend_common.settings import get_settings
from backend_common.submission_lock import submission_lock

router = APIRouter(prefix="/api/app/mcp/files", tags=["agent transfers"])


def identity(request: Request, db: Session = Depends(get_db)):
    validate_local_headers(request.headers)
    client_id = authenticate(db, request.headers.get("authorization", ""))
    client, context = resolve_client(db, client_id)
    expected = request.headers.get("x-neurocade-installation")
    from api_service.mcp_adapter.identity import installation_id
    if expected != installation_id():
        raise HTTPException(409, "Installation identity mismatch; reconnect NeuroCade")
    return client, context


def digest_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@router.put("/upload")
async def upload(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    idempotency_key: str = Query(min_length=1, max_length=255),
    sha256: str = Query(pattern="^[a-f0-9]{64}$"),
    title: str | None = Query(default=None, max_length=255),
    case_id: str | None = None,
    db: Session = Depends(get_db),
    paired=Depends(identity),
):
    client, _context = paired
    if client.access != "standard" or get_settings().mcp_access != "standard":
        raise HTTPException(403, "Read-only connection cannot upload")
    if filename != Path(filename).name or "\\" in filename:
        raise HTTPException(400, "Filename must not contain directories")
    if case_id and title is not None:
        raise HTTPException(400, "Specify title for a new case OR case_id for an existing case, not both.")
    if not case_id:
        # Reject invalid metadata before receiving the scan or claiming a retry key.
        title = validate_case_name_or_400((title or "").strip() or case_title_from_filename(filename))
    if case_id:
        get_case_for_user(db, case_id, client.user_id, workspace_id=client.workspace_id)
    client_id = client.id
    db.commit()
    # Receive binary bytes with a hard limit before invoking the existing importer.
    # No volume data enters the MCP JSON protocol or model context.
    with tempfile.TemporaryFile() as staged:
        size = 0
        digest = hashlib.sha256()
        async for chunk in request.stream():
            size += len(chunk)
            if size > get_settings().max_upload_file_size_bytes:
                raise HTTPException(413, "Upload exceeds the configured file size limit")
            digest.update(chunk)
            await run_in_threadpool(staged.write, chunk)
        if not size:
            raise HTTPException(400, "Empty upload")
        if digest.hexdigest() != sha256:
            raise HTTPException(400, "SHA-256 mismatch")
        staged.seek(0)
        return await run_in_threadpool(import_staged, db, client_id, staged, filename, title, case_id, idempotency_key, sha256)


def import_staged(db, client_id, staged, filename, title, case_id, key, sha256):
    import asyncio

    from api_service.assistant.tool_execution_store import arguments_digest

    with submission_lock:
        db.rollback()
        db.connection(execution_options={"sqlite_begin_immediate": True})
        client, context = resolve_client(db, client_id)
        if client.access != "standard" or get_settings().mcp_access != "standard":
            raise HTTPException(403, "Read-only connection cannot upload")
        if case_id:
            get_case_for_user(db, case_id, client.user_id, workspace_id=client.workspace_id)
        args = dict(filename=filename, title=title, case_id=case_id, sha256=sha256)
        digest = arguments_digest(args)
        row = db.query(AssistantToolExecution).filter_by(client_id=client_id, call_id=key).one_or_none()
        if row:
            db.commit()
            if row.tool_name != "upload_case" or row.arguments_digest != digest:
                raise HTTPException(409, "IDEMPOTENCY_CONFLICT: use the original upload parameters")
            if row.status == "succeeded":
                return row.result_json
            raise HTTPException(409, "Upload is incomplete or invalidated; inspect the case before retrying with a new key")
        if case_id:
            case, _, _, _ = get_case_for_user(db, case_id, client.user_id, workspace_id=client.workspace_id)
            ensure_case_not_active(db, case)
        else:
            try:
                ensure_outputs_idle(db, client.workspace_id, str(uuid4()))
            except OutputBusy as exc:
                raise HTTPException(409, str(exc)) from exc
        row = AssistantToolExecution(source="mcp", client_id=client.id, user_id=client.user_id,
            workspace_id=client.workspace_id, case_id=case_id, call_id=key, tool_name="upload_case",
            arguments_digest=digest, arguments_json=args, risk="write", status="running")
        db.add(row)
        db.commit()
    try:
        upload_file = UploadFile(file=staged, filename=filename)
        if case_id:
            result = asyncio.run(add_upload_to_case(db, context, case_id=case_id, file=upload_file, files=None))
        else:
            result = asyncio.run(create_case_from_upload(db, context, workspace_id=client.workspace_id,
                title=title, description=None, modalities=None, tags=None, notes=None, file=upload_file, files=None))
        body = result.model_dump(mode="json")
        body["sha256"] = sha256
        row.case_id = result.case_id
        row.status = "succeeded"
        row.result_json = body
        db.commit()
        return body
    except HTTPException as exc:
        db.rollback()
        if isinstance(exc.__cause__, OutputBusy):
            # Admission failed before ingestion; preserve this retry key.
            db.delete(row)
        else:
            row.status = "ambiguous"
        db.commit()
        raise
    except Exception:
        db.rollback()
        # Import may have committed before an audit failure. Never replay blindly.
        row.status = "ambiguous"
        db.commit()
        raise


def authorized_artifact(db, paired, artifact_id):
    client, context = paired
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.workspace_id != client.workspace_id:
        raise HTTPException(404, "Artifact not found in this connection's workspace")
    return resolve_artifact_file_for_user(db, context, artifact_id)


@router.get("/artifacts/{artifact_id}")
def download_artifact(artifact_id: str, db: Session = Depends(get_db), paired=Depends(identity)):
    client, context = paired
    artifact, _ = authorized_artifact(db, paired, artifact_id)
    try:
        with reserve_case_files(db, client.workspace_id, artifact.case_id):
            # Resolve again after acquiring the reservation: a rename may have
            # completed between the initial authorization and admission.
            artifact, source = authorized_artifact(db, paired, artifact_id)
            root = snapshot_root()
            with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as staged:
                temporary = Path(staged.name)
            try:
                shutil.copyfile(source, temporary)
                sha256 = digest_file(temporary)
                snapshot = root / (hashlib.sha256((client.id + ":" + artifact.id + ":" + sha256).encode()).hexdigest() + ".bin")
                temporary.replace(snapshot)
            finally:
                temporary.unlink(missing_ok=True)
            log_event(db, context, "mcp.artifact.downloaded", case_id=artifact.case_id,
                      artifact_id=artifact.id, details={"client_id": client.id})
            return FileResponse(snapshot, filename=artifact.name, media_type=artifact.mime_type,
                                headers={"X-Checksum-SHA256": sha256, "ETag": '"' + sha256 + '"', "Cache-Control": "no-store"})
    except OutputBusy as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/cases/{case_id}")
def download_case(case_id: str, db: Session = Depends(get_db), paired=Depends(identity)):
    client, context = paired
    case, _, role, case_dir = get_case_for_user(db, case_id, context.user.id, workspace_id=client.workspace_id)
    require_case_read(role)
    try:
        with reserve_case_files(db, client.workspace_id, case_id):
            case, _, role, case_dir = get_case_for_user(db, case_id, context.user.id, workspace_id=client.workspace_id)
            require_case_read(role)
            return archive_case(db, client, context, case, case_dir)
    except OutputBusy as exc:
        raise HTTPException(409, str(exc)) from exc


def snapshot_root():
    root = get_settings().fs_data_root / ".tmp" / "mcp-downloads"
    root.mkdir(parents=True, exist_ok=True)
    for old in root.iterdir():
        if old.suffix not in {".zip", ".bin"}:
            continue
        with suppress(FileNotFoundError):
            if old.stat().st_mtime < time.time() - 86400:
                old.unlink(missing_ok=True)
    return root


def archive_case(db, client, context, case, case_dir):
    root = snapshot_root()
    manifest = []
    for path in sorted(case_dir.rglob("*")):
        if not path.is_symlink() and path.is_file() and case_dir.resolve() in path.resolve().parents:
            info = path.stat()
            manifest.append((path, str(path.relative_to(case_dir)), info.st_size, info.st_mtime_ns))
    fingerprint = hashlib.sha256(repr((client.id, case.id, manifest)).encode()).hexdigest()
    archive = root / (fingerprint + ".zip")
    if not archive.exists():
        temporary = archive.with_suffix(".tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path, name, _, _ in manifest:
                    output.write(path, arcname=name)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    sha256 = digest_file(archive)
    log_event(db, context, "mcp.case.downloaded", case_id=case.id, details={"client_id": client.id})
    return FileResponse(archive, filename=f"{case.title}.zip", media_type="application/zip",
                        headers={"X-Checksum-SHA256": sha256, "ETag": '"' + sha256 + '"', "Cache-Control": "no-store"})
