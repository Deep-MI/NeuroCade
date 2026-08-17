"""Case upload, DICOM conversion, and input volume storage helpers."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from neurocade_runtime_tools.container_request import DCM2NIIX_IMAGE, RuntimeBind, build_container_request
from neurocade_runtime_tools.execution import RuntimeExecutionPolicy, RuntimeExecutionRequest, execute_runtime_request
from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.case_storage import (
    case_named_upload,
    ensure_case_storage_layout,
    unique_upload_name,
)
from backend_common.db import Artifact, ArtifactKind, Case, Workspace
from backend_common.storage import resolve_artifact_path

DIRECT_VOLUME_SUFFIXES = (".nii.gz", ".nii", ".mgz")
DICOM_UPLOAD_SUFFIXES = (".dcm", ".dicom", ".ima")

def _require_run_analysis_input_artifact(db: Session, case: Case, artifact_id: str | None) -> Artifact:
    """Return a selected intensity-volume input or raise an API error."""
    if not artifact_id:
        raise HTTPException(status_code=400, detail="Workflow input artifact id is required")
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Workflow input artifact not found")
    if artifact.case_id != case.id:
        raise HTTPException(status_code=400, detail="Workflow input artifact does not belong to this case")
    if artifact.kind != ArtifactKind.volume or (artifact.metadata_json or {}).get("volume_role", "intensity") != "intensity":
        raise HTTPException(status_code=400, detail="Run Analysis inputs must be intensity-volume artifacts")
    artifact_path = resolve_artifact_path(artifact)
    if not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Workflow input artifact is missing on disk")
    return artifact


def _unique_upload_name_for_case(db: Session, case: Case, workspace: Workspace, case_dir: Path, source_name: str) -> str:
    """Return a non-conflicting upload filename across disk and artifact rows."""
    candidate = unique_upload_name(case_dir, source_name)
    stem = Path(candidate).stem
    if candidate.lower().endswith(".nii.gz"):
        stem = candidate[:-7]
    ext = "".join(Path(candidate).suffixes) if candidate.lower().endswith(".nii.gz") else Path(candidate).suffix
    artifact_paths = {
        relative_path
        for (relative_path,) in db.query(Artifact.relative_path)
        .filter(Artifact.case_id == case.id)
        .all()
    }
    index = 2
    while (case_dir / candidate).exists() or candidate in artifact_paths:
        candidate = f"{stem}-{index}{ext}"
        index += 1
    return candidate


async def _write_upload_file(file: UploadFile, target_path: Path) -> tuple[int, str]:
    """Stream an uploaded file to disk while enforcing the configured size limit."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    file.file.seek(0)
    size_bytes = 0
    with target_path.open("wb") as handle:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            size_bytes += len(chunk)
            if size_bytes > settings.max_upload_file_size_bytes:
                handle.close()
                target_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Upload exceeds the configured file size limit")
            handle.write(chunk)
    return size_bytes, file.content_type or "application/octet-stream"


def _validate_uploaded_volume_header(path: Path, source_name: str) -> None:
    """Reject malformed direct MRI volume uploads before creating an artifact."""
    lower = source_name.lower()
    try:
        header = path.read_bytes()[:352]
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Uploaded MRI file could not be read") from exc
    if lower.endswith((".mgz", ".nii.gz")):
        if not header.startswith(b"\x1f\x8b"):
            raise HTTPException(status_code=400, detail="Compressed MRI uploads must be gzip-encoded")
        return
    if lower.endswith(".nii"):
        if len(header) < 348:
            raise HTTPException(status_code=400, detail="NIfTI upload is too small to contain a valid header")
        if header[344:348] not in {b"n+1\x00", b"ni1\x00"}:
            raise HTTPException(status_code=400, detail="NIfTI upload has an invalid header")


def _validate_dicom_source_header(path: Path, source_name: str) -> None:
    """Reject malformed DICOM files and ZIP archives before conversion."""
    lower = source_name.lower()
    if lower.endswith(".zip"):
        if not zipfile.is_zipfile(path):
            raise HTTPException(status_code=400, detail="DICOM ZIP upload is not a valid ZIP archive")
        return
    try:
        header = path.read_bytes()[:132]
    except OSError as exc:
        raise HTTPException(status_code=400, detail="DICOM upload could not be read") from exc
    if len(header) >= 132 and header[128:132] != b"DICM":
        raise HTTPException(status_code=400, detail="DICOM upload is missing the DICM header")


def _upload_filename(file: UploadFile) -> str:
    return Path(file.filename or "upload").name


def _lower_upload_name(file: UploadFile) -> str:
    return _upload_filename(file).lower()


def _has_suffix(filename: str, suffixes: tuple[str, ...]) -> bool:
    lower = filename.lower()
    return any(lower.endswith(suffix) for suffix in suffixes)


def _is_direct_volume_upload(file: UploadFile) -> bool:
    return _has_suffix(_lower_upload_name(file), DIRECT_VOLUME_SUFFIXES)


def _is_dicom_source_upload(file: UploadFile) -> bool:
    name = _lower_upload_name(file)
    return name.endswith(".zip") or _has_suffix(name, DICOM_UPLOAD_SUFFIXES)


def _collect_upload_files(file: UploadFile | None, files: list[UploadFile] | None) -> list[UploadFile]:
    """Normalize single-file and multi-file request inputs into one upload list."""
    collected: list[UploadFile] = []
    if file is not None and hasattr(file, "filename") and hasattr(file, "file"):
        collected.append(file)
    if isinstance(files, list):
        collected.extend(upload for upload in files if upload is not None)
    if not collected:
        raise HTTPException(status_code=400, detail="No upload file provided")
    return collected


def _safe_storage_filename(filename: str, fallback_stem: str = "converted") -> str:
    """Create a filesystem-safe NIfTI storage name from an uploaded or converted filename."""
    source = Path(filename).name
    lower = source.lower()
    if lower.endswith(".nii.gz"):
        stem = source[:-7]
        ext = ".nii.gz"
    else:
        path = Path(source)
        stem = path.stem
        ext = path.suffix or ".nii.gz"
    stem = re.sub(r'[/\\:*?"<>|\s]+', "_", stem).strip("._ ")
    if not stem:
        stem = fallback_stem
    if ext.lower() not in {".nii", ".gz"} and not lower.endswith(".nii.gz"):
        ext = ".nii.gz"
    return f"{stem}{ext}"


def _safe_source_filename(filename: str, fallback_stem: str) -> str:
    """Create a filesystem-safe source name while preserving sanitized suffixes."""
    source = Path(filename).name
    suffixes = "".join(Path(source).suffixes)
    stem = source[: -len(suffixes)] if suffixes else source
    stem = re.sub(r'[/\\:*?"<>|\s]+', "_", stem).strip("._ ")
    if not stem:
        stem = fallback_stem
    safe_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffixes)[:32]
    return f"{stem}{safe_suffix}"


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    """Extract a DICOM ZIP after checking size, entry count, and path traversal risks."""
    total_entries = 0
    total_size = 0
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member.is_dir():
                continue
            total_entries += 1
            total_size += member.file_size
            if total_entries > settings.dicom_zip_max_entries:
                raise HTTPException(status_code=413, detail="DICOM ZIP contains too many files")
            if total_size > settings.dicom_zip_max_expanded_bytes:
                raise HTTPException(status_code=413, detail="DICOM ZIP expanded size exceeds the configured limit")
            if member_path.is_absolute() or ".." in member_path.parts:
                raise HTTPException(status_code=400, detail="DICOM ZIP contains unsafe paths")
            destination = (target_dir / member_path).resolve()
            if target_dir.resolve() not in destination.parents:
                raise HTTPException(status_code=400, detail="DICOM ZIP contains unsafe paths")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _run_dcm2niix(input_dir: Path, output_dir: Path) -> None:
    """Convert staged DICOM files with the configured dcm2niix container."""
    command = ["dcm2niix", "-z", "y", "-b", "y", "-ba", "y", "-o", "/output", "-f", "%p_%s", "/input"]
    binds = [
        RuntimeBind(input_dir, "/input", "ro"),
        RuntimeBind(output_dir, "/output", "rw"),
    ]
    cmd = build_container_request(
        image=os.environ.get("NEUROCADE_DCM2NIIX_IMAGE", DCM2NIIX_IMAGE),
        binds=binds,
        disable_network=True,
        command=command,
    )
    try:
        result = execute_runtime_request(
            RuntimeExecutionRequest(
                argv=[],
                cwd=output_dir,
                timeout_s=settings.dicom_conversion_timeout_seconds,
                execution_mode="container",
                output_root=output_dir,
                workdir_root=output_dir,
                runtime_policy=RuntimeExecutionPolicy(
                    network_disabled=True,
                    gpu_enabled=False,
                ),
                container_run=cmd,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Apptainer is not installed or not on PATH") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="DICOM conversion timed out") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "dcm2niix failed"
        raise HTTPException(status_code=400, detail=f"DICOM conversion failed: {stderr[-1000:]}")


async def _stage_dicom_sources(upload_files: list[UploadFile], input_dir: Path, raw_dir: Path) -> None:
    """Write DICOM uploads to temporary raw storage and stage them for conversion."""
    for index, upload in enumerate(upload_files, start=1):
        source_name = _upload_filename(upload)
        staged_name = _safe_source_filename(source_name, fallback_stem=f"dicom-{index}")
        staged_path = raw_dir / staged_name
        await _write_upload_file(upload, staged_path)
        _validate_dicom_source_header(staged_path, source_name)
        if source_name.lower().endswith(".zip"):
            _safe_extract_zip(staged_path, input_dir / Path(staged_name).stem)
        else:
            target_path = input_dir / staged_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_path, target_path)


def _create_volume_artifact(
    db: Session,
    case: Case,
    workspace: Workspace,
    target_path: Path,
    case_dir: Path,
    *,
    metadata: dict,
    mime_type: str | None = None,
) -> Artifact:
    """Create a volume artifact for a stored path."""
    artifact = Artifact(
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name=target_path.name,
        relative_path=str(target_path.relative_to(case_dir)),
        mime_type=mime_type
        or ("application/gzip" if target_path.name.lower().endswith(".gz") else "application/octet-stream"),
        size_bytes=target_path.stat().st_size,
        metadata_json={
            "volume_role": "intensity",
            **metadata,
        },
    )
    db.add(artifact)
    db.flush()
    return artifact


async def _store_case_upload(
    db: Session,
    case: Case,
    workspace: Workspace,
    file: UploadFile,
    *,
    name_after_case: bool,
) -> Artifact:
    """Store a direct MRI volume upload and register it as a case artifact."""
    case_dir = ensure_case_storage_layout(settings, case, workspace)
    source_name = file.filename or "upload.nii.gz"
    stored_name = case_named_upload(case.title, source_name) if name_after_case else _unique_upload_name_for_case(db, case, workspace, case_dir, source_name)
    target_path = case_dir / stored_name
    _size_bytes, mime_type = await _write_upload_file(file, target_path)
    try:
        _validate_uploaded_volume_header(target_path, source_name)
    except HTTPException:
        target_path.unlink(missing_ok=True)
        raise
    return _create_volume_artifact(
        db,
        case,
        workspace,
        target_path,
        case_dir,
        metadata={"source": "upload"},
        mime_type=mime_type,
    )


async def _store_case_dicom_uploads(
    db: Session,
    case: Case,
    workspace: Workspace,
    upload_files: list[UploadFile],
) -> list[Artifact]:
    """Convert DICOM uploads to NIfTI files and register the resulting artifacts."""
    case_dir = ensure_case_storage_layout(settings, case, workspace)
    with tempfile.TemporaryDirectory(prefix="dicom-upload-") as temp_root_name:
        temp_root = Path(temp_root_name)
        input_dir = temp_root / "input"
        output_dir = temp_root / "converted"
        raw_dir = temp_root / "raw"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        await _stage_dicom_sources(upload_files, input_dir, raw_dir)
        _run_dcm2niix(input_dir, output_dir)

        converted_paths = sorted(
            {
                path
                for pattern in ("*.nii", "*.nii.gz")
                for path in output_dir.rglob(pattern)
                if path.is_file()
            },
            key=lambda path: path.name,
        )
        if not converted_paths:
            raise HTTPException(status_code=400, detail="DICOM conversion produced no NIfTI volume")

        created_artifacts: list[Artifact] = []

        for index, converted_path in enumerate(converted_paths, start=1):
            stored_name = unique_upload_name(
                case_dir,
                _safe_storage_filename(converted_path.name, fallback_stem=f"converted-{index}"),
            )
            target_path = case_dir / stored_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(converted_path, target_path)
            artifact = _create_volume_artifact(
                db,
                case,
                workspace,
                target_path,
                case_dir,
                metadata={
                    "source": "dicom-upload",
                    "dicom_converted": True,
                    "original_converted_name": converted_path.name,
                },
            )
            created_artifacts.append(artifact)

    return created_artifacts


async def _store_uploaded_inputs(
    db: Session,
    case: Case,
    workspace: Workspace,
    upload_files: list[UploadFile],
    *,
    name_after_case: bool,
) -> list[Artifact]:
    """Route validated uploads to the direct-volume or DICOM conversion storage path."""
    direct_uploads = [upload for upload in upload_files if _is_direct_volume_upload(upload)]
    dicom_uploads = [upload for upload in upload_files if _is_dicom_source_upload(upload)]
    unknown_uploads = [
        _upload_filename(upload)
        for upload in upload_files
        if upload not in direct_uploads and upload not in dicom_uploads
    ]
    if unknown_uploads:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported upload file type: {', '.join(unknown_uploads)}",
        )
    if direct_uploads and (dicom_uploads or len(upload_files) > 1):
        raise HTTPException(status_code=400, detail="Upload either one MRI volume or a DICOM series, not both")
    if direct_uploads:
        return [
            await _store_case_upload(
                db,
                case,
                workspace,
                direct_uploads[0],
                name_after_case=name_after_case,
            )
        ]
    return await _store_case_dicom_uploads(
        db,
        case,
        workspace,
        dicom_uploads,
    )
