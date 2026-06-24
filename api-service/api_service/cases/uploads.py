"""Case upload, DICOM conversion, and input volume storage helpers."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.runtime.execution import execute_runtime_request
from backend_common.case_storage import (
    canonical_upload_name,
    case_relative_prefix,
    ensure_case_storage_layout,
    unique_upload_name,
)
from backend_common.db import Artifact, ArtifactKind, Case, Workspace
from backend_common.scan import classify_volume_metadata
from neurocade_runtime_tools.container_request import RuntimeBind, build_container_request, core_container_image
from neurocade_runtime_tools.execution import RuntimeExecutionPolicy, RuntimeExecutionRequest

DIRECT_VOLUME_SUFFIXES = (".nii.gz", ".nii", ".mgz")
DICOM_UPLOAD_SUFFIXES = (".dcm", ".dicom", ".ima")
STRUCTURAL_SERIES_HINTS = (
    "t1",
    "t1w",
    "mprage",
    "bravo",
    "spgr",
    "mp2rage",
    "tfe",
    "struct",
    "anatom",
)
NON_STRUCTURAL_SERIES_HINTS = (
    "localizer",
    "locator",
    "scout",
    "dwi",
    "diff",
    "dti",
    "bold",
    "fmri",
    "func",
    "asl",
    "fieldmap",
    "fmap",
    "adc",
    "phase",
    "phasediff",
    "trace",
    "fa",
)

def _artifact_disk_path(relative_path: str) -> Path:
    return settings.fs_data_root / relative_path


def _relative_data_path(path: Path) -> str:
    return str(path.resolve().relative_to(settings.fs_data_root.resolve()))


def _require_input_volume_artifact(db: Session, case: Case, artifact_id: str | None) -> Artifact:
    """Return the selected FastSurfer input volume or raise an API error."""
    if not artifact_id:
        raise HTTPException(status_code=400, detail="FastSurfer input_artifact_id is required")
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="FastSurfer input artifact not found")
    if artifact.case_id != case.id:
        raise HTTPException(status_code=400, detail="FastSurfer input artifact does not belong to this case")
    if artifact.kind != ArtifactKind.volume:
        raise HTTPException(status_code=400, detail="FastSurfer input artifact must be a volume")
    if (artifact.metadata_json or {}).get("volume_role") == "segmentation":
        raise HTTPException(status_code=400, detail="FastSurfer input artifact must be an intensity volume")
    artifact_path = _artifact_disk_path(artifact.relative_path)
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="FastSurfer input artifact is missing on disk")
    return artifact


def _container_path(path: Path) -> str:
    relative = Path(_relative_data_path(path))
    return f"/data/{relative.as_posix()}"


def _unique_upload_name_for_case(db: Session, case: Case, workspace: Workspace, case_dir: Path, source_name: str) -> str:
    """Return a non-conflicting upload filename across disk and artifact rows."""
    candidate = unique_upload_name(case_dir, source_name)
    stem = Path(candidate).stem
    if candidate.lower().endswith(".nii.gz"):
        stem = candidate[:-7]
    ext = "".join(Path(candidate).suffixes) if candidate.lower().endswith(".nii.gz") else Path(candidate).suffix
    prefix = case_relative_prefix(workspace.id, case.id)
    artifact_paths = {
        relative_path
        for (relative_path,) in db.query(Artifact.relative_path)
        .filter(Artifact.case_id == case.id)
        .all()
    }
    index = 2
    while (case_dir / candidate).exists() or f"{prefix}/{candidate}" in artifact_paths:
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


def _series_metadata_for_volume(volume_path: Path) -> dict:
    """Read dcm2niix JSON sidecar metadata for a converted volume when present."""
    sidecar = volume_path.with_suffix("")
    if volume_path.name.lower().endswith(".nii.gz"):
        sidecar = volume_path.with_suffix("").with_suffix(".json")
    elif volume_path.suffix.lower() == ".nii":
        sidecar = volume_path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _score_converted_volume(volume_path: Path) -> tuple[int, str]:
    """Score a converted volume by structural MRI metadata hints and filename."""
    metadata = _series_metadata_for_volume(volume_path)
    haystack = " ".join(
        str(value)
        for key, value in metadata.items()
        if key.lower() in {"seriesdescription", "protocolname", "sequencename", "imagecomments"}
    )
    haystack = f"{haystack} {volume_path.name}".lower()
    score = 0
    matched_structural = [hint for hint in STRUCTURAL_SERIES_HINTS if hint in haystack]
    matched_non_structural = [hint for hint in NON_STRUCTURAL_SERIES_HINTS if hint in haystack]
    score += 100 * len(matched_structural)
    score -= 150 * len(matched_non_structural)
    if "3d" in haystack:
        score += 20
    if "sag" in haystack or "sagittal" in haystack:
        score += 10
    reason = "structural series hint" if matched_structural else "largest converted volume fallback"
    if matched_non_structural and not matched_structural:
        reason = "largest converted volume fallback after non-structural hint penalty"
    return score, reason


def _select_converted_input_volume(volume_paths: list[Path]) -> tuple[Path, str]:
    """Choose the most likely anatomical input volume from dcm2niix outputs."""
    if len(volume_paths) == 1:
        return volume_paths[0], "single converted output"
    ranked = sorted(
        volume_paths,
        key=lambda path: (_score_converted_volume(path)[0], path.stat().st_size, path.name),
        reverse=True,
    )
    selected = ranked[0]
    _score, reason = _score_converted_volume(selected)
    return selected, reason


def _run_dcm2niix(input_dir: Path, output_dir: Path) -> None:
    """Convert staged DICOM files with the configured dcm2niix container."""
    command = ["dcm2niix", "-z", "y", "-b", "y", "-ba", "y", "-o", "/output", "-f", "%p_%s", "/input"]
    binds = [
        RuntimeBind(input_dir, "/input", "ro"),
        RuntimeBind(output_dir, "/output", "rw"),
    ]
    try:
        image = core_container_image("dcm2niix")
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="dcm2niix container image is not configured") from exc
    cmd = build_container_request(
        image=image,
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
        raise HTTPException(status_code=500, detail="Docker is not installed or not on PATH") from exc
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


def _store_raw_dicom_archive(
    db: Session,
    case: Case,
    workspace: Workspace,
    case_dir: Path,
    raw_dir: Path,
) -> Artifact | None:
    """Persist a ZIP artifact with the original DICOM uploads when retention is enabled."""
    if settings.dicom_raw_retention.strip().lower() != "archive":
        return None
    archive_name = unique_upload_name(case_dir, "raw-dicom.zip")
    archive_path = case_dir / archive_name
    shutil.make_archive(str(archive_path.with_suffix("")), "zip", raw_dir)
    if archive_path.with_suffix(".zip") != archive_path:
        archive_path = archive_path.with_suffix(".zip")
    artifact = Artifact(
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.derived,
        name=archive_path.name,
        relative_path=_relative_data_path(archive_path),
        mime_type="application/zip",
        size_bytes=archive_path.stat().st_size,
        metadata_json={"source": "dicom-upload", "dicom_raw": True},
    )
    db.add(artifact)
    db.flush()
    return artifact


def _create_volume_artifact(
    db: Session,
    case: Case,
    workspace: Workspace,
    target_path: Path,
    *,
    metadata: dict,
    source_name: str | None = None,
    mime_type: str | None = None,
) -> Artifact:
    """Create a volume artifact for a stored path."""
    artifact = Artifact(
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name=target_path.name,
        relative_path=_relative_data_path(target_path),
        mime_type=mime_type
        or ("application/gzip" if target_path.name.lower().endswith(".gz") else "application/octet-stream"),
        size_bytes=target_path.stat().st_size,
        metadata_json={
            **classify_volume_metadata(source_name or target_path.name),
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
    use_canonical_name: bool,
) -> Artifact:
    """Store a direct MRI volume upload and register it as a case artifact."""
    case_dir = ensure_case_storage_layout(db, settings, case, workspace)
    source_name = file.filename or "upload.nii.gz"
    stored_name = canonical_upload_name(case.title, source_name) if use_canonical_name else _unique_upload_name_for_case(db, case, workspace, case_dir, source_name)
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
        metadata={"source": "upload"},
        source_name=source_name,
        mime_type=mime_type,
    )


async def _store_case_dicom_uploads(
    db: Session,
    case: Case,
    workspace: Workspace,
    upload_files: list[UploadFile],
    *,
    use_canonical_name: bool,
) -> Artifact:
    """Convert DICOM uploads to NIfTI files and register the resulting artifacts."""
    case_dir = ensure_case_storage_layout(db, settings, case, workspace)
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

        selected_input, selection_reason = _select_converted_input_volume(converted_paths)
        created_artifacts: list[Artifact] = []
        selected_artifact: Artifact | None = None

        for index, converted_path in enumerate(converted_paths, start=1):
            is_selected = converted_path == selected_input
            if is_selected and use_canonical_name:
                stored_name = canonical_upload_name(case.title, converted_path.name)
            else:
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
                metadata={
                    "source": "dicom-upload",
                    "dicom_converted": True,
                    "dicom_selected_input_candidate": is_selected,
                    "dicom_input_selection_reason": selection_reason if is_selected else None,
                    "original_converted_name": converted_path.name,
                },
            )
            created_artifacts.append(artifact)
            if is_selected:
                selected_artifact = artifact

        _store_raw_dicom_archive(db, case, workspace, case_dir, raw_dir)

    if selected_artifact is not None:
        return selected_artifact
    return created_artifacts[0]


async def _store_uploaded_inputs(
    db: Session,
    case: Case,
    workspace: Workspace,
    upload_files: list[UploadFile],
    *,
    use_canonical_name: bool,
) -> Artifact:
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
        return await _store_case_upload(db, case, workspace, direct_uploads[0], use_canonical_name=use_canonical_name)
    return await _store_case_dicom_uploads(db, case, workspace, dicom_uploads, use_canonical_name=use_canonical_name)


def _copy_input_artifact_to_case(
    db: Session,
    source_artifact: Artifact,
    target_case: Case,
    workspace: Workspace,
) -> Artifact:
    """Copy a selected input volume artifact into another case."""
    source_path = _artifact_disk_path(source_artifact.relative_path)
    target_dir = ensure_case_storage_layout(db, settings, target_case, workspace)
    target_name = canonical_upload_name(target_case.title, source_artifact.name)
    target_path = target_dir / target_name
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return _create_volume_artifact(
        db,
        target_case,
        workspace,
        target_path,
        metadata=source_artifact.metadata_json or {},
        mime_type=source_artifact.mime_type,
    )
