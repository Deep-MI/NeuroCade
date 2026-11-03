"""Case upload, DICOM conversion, and input volume storage helpers."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.runtime.dicom_conversion import run_dcm2niix as _run_dcm2niix
from backend_common.artifact_classification import classify_import_artifact
from backend_common.case_storage import (
    case_named_upload,
    ensure_case_storage_layout,
    unique_upload_name,
)
from backend_common.db import Artifact, ArtifactKind, Case, Workspace

DIRECT_VOLUME_SUFFIXES = (".nii.gz", ".nii", ".mgz")
DICOM_UPLOAD_SUFFIXES = (".dcm", ".dicom", ".ima")
SEGMENTATION_NAMES = {
    "aparc+aseg.mgz",
    "wmparc.mgz",
    "aseg.mgz",
    "aparc.dktatlas+aseg.deep.mgz",
    "aseg.auto_noccseg.mgz",
}
FREESURFER_TEMPLATE_DIRS = {"fsaverage", "fsaverage3", "fsaverage4", "fsaverage5", "fsaverage6", "cvs_avg35inmni152"}


def _unique_upload_name_for_case(db: Session, case: Case, workspace: Workspace, case_dir: Path, source_name: str) -> str:
    """Return a non-conflicting upload filename across disk and artifact rows."""
    candidate = unique_upload_name(case_dir, source_name)
    stem = Path(candidate).stem
    if candidate.lower().endswith(".nii.gz"):
        stem = candidate[:-7]
    ext = "".join(Path(candidate).suffixes) if candidate.lower().endswith(".nii.gz") else Path(candidate).suffix
    artifact_paths = {relative_path for (relative_path,) in db.query(Artifact.relative_path).filter(Artifact.case_id == case.id).all()}
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
        with path.open("rb") as handle:
            header = handle.read(352)
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
        with path.open("rb") as handle:
            header = handle.read(132)
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
    if len(collected) > settings.max_upload_files:
        raise HTTPException(status_code=413, detail=f"Upload contains more than {settings.max_upload_files} files")
    declared_size = sum(upload.size for upload in collected if isinstance(upload.size, int) and upload.size >= 0)
    if declared_size > settings.max_upload_batch_size_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds the configured total size limit")
    return collected


def _safe_relative_upload_path(value: str) -> PurePosixPath:
    """Validate a browser-provided path without silently changing its meaning."""
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise HTTPException(status_code=400, detail="Upload relative path is invalid")
    if "\\" in value:
        raise HTTPException(status_code=400, detail="Upload relative paths must use forward slashes")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise HTTPException(status_code=400, detail=f"Unsafe upload relative path: {value}")
    if any(len(part) > 255 for part in path.parts):
        raise HTTPException(status_code=400, detail="Upload relative path component is too long")
    return path


def _parse_relative_upload_paths(
    relative_paths: str | None,
    upload_files: list[UploadFile],
) -> list[PurePosixPath] | None:
    """Parse the ordered JSON path manifest supplied alongside multipart files."""
    if relative_paths is None:
        raw_paths = [upload.filename or "upload" for upload in upload_files]
        # A plain legacy filename does not opt into directory-preserving import.
        if not any("/" in value or "\\" in value for value in raw_paths):
            return None
    else:
        try:
            raw_paths = json.loads(relative_paths)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="relative_paths must be a JSON array") from exc
        if not isinstance(raw_paths, list) or len(raw_paths) != len(upload_files):
            raise HTTPException(status_code=400, detail="relative_paths must match the uploaded files")
    paths = [_safe_relative_upload_path(value) for value in raw_paths]
    folded = [path.as_posix().casefold() for path in paths]
    if len(folded) != len(set(folded)):
        raise HTTPException(status_code=400, detail="Upload contains duplicate relative paths")
    return paths


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


async def _stage_dicom_sources(
    upload_files: list[UploadFile],
    input_dir: Path,
    raw_dir: Path,
    relative_paths: list[PurePosixPath] | None = None,
) -> None:
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
            relative_path = relative_paths[index - 1] if relative_paths else PurePosixPath(staged_name)
            target_path = input_dir.joinpath(*relative_path.parts)
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
        mime_type=mime_type or ("application/gzip" if target_path.name.lower().endswith(".gz") else "application/octet-stream"),
        size_bytes=target_path.stat().st_size,
        metadata_json={
            "volume_role": "intensity",
            **metadata,
        },
    )
    db.add(artifact)
    db.flush()
    return artifact


def _logical_tree_paths(paths: list[PurePosixPath]) -> tuple[str | None, list[PurePosixPath]]:
    """Remove a browser folder picker's common wrapper directory for recognition only."""
    if paths and all(len(path.parts) > 1 for path in paths):
        roots = {path.parts[0] for path in paths}
        if len(roots) == 1:
            root = next(iter(roots))
            return root, [PurePosixPath(*path.parts[1:]) for path in paths]
    return None, paths


def _detect_uploaded_layout(paths: list[PurePosixPath]) -> dict[str, object]:
    """Conservatively identify well-known folder layouts from their manifest."""
    root_name, logical = _logical_tree_paths(paths)
    strings = {path.as_posix() for path in logical}
    first_parts = {path.parts[0] for path in logical if path.parts}
    subject_ids = sorted(part for part in first_parts if part.startswith("sub-") and len(part) > 4)
    if "dataset_description.json" in strings and subject_ids:
        return {"format": "bids", "scope": "dataset", "subject_ids": subject_ids}
    if (
        root_name
        and root_name.startswith("sub-")
        and any(path.parts and path.parts[0] in {"anat", "dwi", "func", "fmap", "perf"} for path in logical)
    ):
        return {"format": "bids", "scope": "subject", "subject_ids": [root_name]}

    has_mri = any(path.parts and path.parts[0] == "mri" for path in logical)
    characteristic = {path.parts[0] for path in logical if path.parts} & {"stats", "surf", "label", "scripts"}
    if has_mri and characteristic:
        fastsurfer = any("deep" in path.name.lower() or "fastsurfer" in path.as_posix().lower() for path in logical)
        return {
            "format": "fastsurfer" if fastsurfer else "freesurfer",
            "scope": "subject",
            "subject_ids": [root_name] if root_name else [],
        }

    subjects: list[str] = []
    fastsurfer = False
    for candidate in sorted(first_parts):
        if candidate.startswith(".") or candidate.casefold() in FREESURFER_TEMPLATE_DIRS:
            continue
        child_paths = [PurePosixPath(*path.parts[1:]) for path in logical if path.parts[0] == candidate]
        child_roots = {path.parts[0] for path in child_paths if path.parts}
        if "mri" in child_roots and child_roots & {"stats", "surf", "label", "scripts"}:
            subjects.append(candidate)
            fastsurfer = fastsurfer or any("deep" in path.name.lower() for path in child_paths)
    if subjects:
        return {
            "format": "fastsurfer" if fastsurfer else "freesurfer",
            "scope": "subjects-directory",
            "subject_ids": subjects,
        }
    return {"format": "generic", "scope": "folder" if any(len(path.parts) > 1 for path in paths) else "files", "subject_ids": []}


def _group_multi_subject_uploads(
    upload_files: list[UploadFile],
    relative_paths: list[PurePosixPath],
    layout: dict[str, object],
) -> dict[str, tuple[list[UploadFile], list[PurePosixPath]]]:
    """Split a dataset manifest into self-contained per-subject upload groups."""
    subject_value = layout.get("subject_ids")
    if layout.get("scope") not in {"dataset", "subjects-directory"} or not isinstance(subject_value, list):
        return {}
    subject_ids = [str(value) for value in subject_value if isinstance(value, str) and value]
    if not subject_ids:
        return {}
    _root_name, logical_paths = _logical_tree_paths(relative_paths)
    groups: dict[str, tuple[list[UploadFile], list[PurePosixPath]]] = {subject_id: ([], []) for subject_id in subject_ids}
    common: list[tuple[UploadFile, PurePosixPath]] = []
    for upload, logical_path in zip(upload_files, logical_paths, strict=True):
        matched = next((subject_id for subject_id in subject_ids if subject_id in logical_path.parts), None)
        if matched is None:
            common.append((upload, logical_path))
            continue
        subject_index = logical_path.parts.index(matched)
        in_case_parts = (*logical_path.parts[:subject_index], *logical_path.parts[subject_index + 1 :])
        if not in_case_parts:
            continue
        group_files, group_paths = groups[matched]
        group_files.append(upload)
        group_paths.append(PurePosixPath(matched, *in_case_parts))

    for subject_id, (group_files, group_paths) in groups.items():
        for upload, logical_path in common:
            group_files.append(upload)
            group_paths.append(PurePosixPath(subject_id, "_dataset", *logical_path.parts))
    return {subject_id: group for subject_id, group in groups.items() if group[0]}


def _uploaded_artifact_metadata(path: PurePosixPath, layout: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {"source": "upload", "source_relative_path": path.as_posix()}
    fmt = str(layout["format"])
    if fmt != "generic":
        metadata["source_format"] = fmt
    lowered = path.name.lower()
    if lowered.endswith((".mgz", ".mgh", ".nii", ".nii.gz")):
        if lowered in SEGMENTATION_NAMES or any(token in lowered for token in ("_seg.", "_dseg.", "segmentation")):
            metadata.update({"volume_role": "segmentation", "lut": "freesurfer" if fmt in {"freesurfer", "fastsurfer"} else "binary"})
        else:
            metadata["volume_role"] = "intensity"
    return metadata


def _unique_relative_target(case_dir: Path, relative_path: PurePosixPath, existing: set[str]) -> Path:
    """Keep a relative directory while suffixing only the basename on collision."""
    parent = PurePosixPath(*relative_path.parts[:-1])
    source = relative_path.name
    suffixes = "".join(Path(source).suffixes)
    stem = source[: -len(suffixes)] if suffixes else source
    candidate = relative_path
    index = 2
    while candidate.as_posix() in existing or (case_dir.joinpath(*candidate.parts)).exists():
        candidate = parent / f"{stem}-{index}{suffixes}"
        index += 1
    existing.add(candidate.as_posix())
    return case_dir.joinpath(*candidate.parts)


async def _store_folder_or_generic_uploads(
    db: Session,
    case: Case,
    workspace: Workspace,
    upload_files: list[UploadFile],
    relative_paths: list[PurePosixPath],
    *,
    initialize_viewer: bool,
) -> tuple[list[Artifact], list[str], dict[str, object], list[dict[str, object]]]:
    """Store an arbitrary file tree and enrich recognizable artifacts."""
    case_dir = ensure_case_storage_layout(settings, case, workspace)
    layout = _detect_uploaded_layout(relative_paths)
    _root_name, storage_paths = _logical_tree_paths(relative_paths)
    existing = {row for (row,) in db.query(Artifact.relative_path).filter(Artifact.case_id == case.id).all()}
    artifacts: list[Artifact] = []
    written: list[Path] = []
    try:
        for upload, source_path, storage_path in zip(upload_files, relative_paths, storage_paths, strict=True):
            target_path = _unique_relative_target(case_dir, storage_path, existing)
            size_bytes, _upload_mime = await _write_upload_file(upload, target_path)
            written.append(target_path)
            metadata = _uploaded_artifact_metadata(source_path, layout)
            classification = classify_import_artifact(target_path, metadata=metadata)
            if classification is None:
                continue
            artifact = Artifact(
                case_id=case.id,
                workspace_id=workspace.id,
                kind=classification.kind,
                name=target_path.name,
                relative_path=str(target_path.relative_to(case_dir)),
                mime_type=classification.mime_type,
                size_bytes=size_bytes,
                metadata_json=classification.metadata,
            )
            db.add(artifact)
            db.flush()
            artifacts.append(artifact)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise

    counts: dict[str, int] = {}
    for artifact in artifacts:
        counts[artifact.kind.value] = counts.get(artifact.kind.value, 0) + 1
    summary = {
        **layout,
        "file_count": len(written),
        "indexed_file_count": len(artifacts),
        "artifact_counts": counts,
    }
    default_layers = _suggest_default_layers(artifacts) if initialize_viewer and layout["scope"] == "subject" else []
    selected_ids = {str(layer["artifact_id"]) for layer in default_layers}
    for artifact in artifacts:
        if artifact.kind == ArtifactKind.volume:
            artifact.metadata_json = {**(artifact.metadata_json or {}), "visible": artifact.id in selected_ids}
    return artifacts, [path.relative_to(case_dir).as_posix() for path in written], summary, default_layers


def _suggest_default_layers(artifacts: list[Artifact]) -> list[dict[str, object]]:
    """Choose one anatomical base and one segmentation for a fresh recognized subject."""
    intensity_order = ["orig.mgz", "t1.mgz", "brain.mgz", "brainmask.mgz"]
    segmentation_order = [
        "aparc.dktatlas+aseg.deep.mgz",
        "aparc+aseg.mgz",
        "wmparc.mgz",
        "aseg.auto_noccseg.mgz",
        "aseg.mgz",
    ]
    volumes = [artifact for artifact in artifacts if artifact.kind == ArtifactKind.volume]
    suggestions: list[dict[str, object]] = []
    for role, order in (("intensity", intensity_order), ("segmentation", segmentation_order)):
        ranked = sorted(
            (artifact for artifact in volumes if (artifact.metadata_json or {}).get("volume_role") == role),
            key=lambda artifact: order.index(artifact.name.lower()) if artifact.name.lower() in order else len(order),
        )
        artifact = ranked[0] if ranked and ranked[0].name.lower() in order else None
        if artifact is None and role == "intensity":
            artifact = next(
                (item for item in ranked if (item.metadata_json or {}).get("source_format") == "bids" and "_t1w" in item.name.lower()),
                None,
            )
        if artifact is None and role == "segmentation":
            artifact = next(
                (item for item in ranked if (item.metadata_json or {}).get("source_format") == "bids"),
                None,
            )
        if artifact is not None:
            suggestions.append(
                {
                    "artifact_id": artifact.id,
                    "filename": artifact.relative_path,
                    "type": role,
                    "visible": True,
                    **({"opacity": 0.45, "lut": (artifact.metadata_json or {}).get("lut", "freesurfer")} if role == "segmentation" else {}),
                }
            )
    return suggestions


async def _store_case_upload(
    db: Session,
    case: Case,
    workspace: Workspace,
    file: UploadFile,
    *,
    name_after_case: bool,
    visible: bool,
) -> Artifact:
    """Store a direct MRI volume upload and register it as a case artifact."""
    case_dir = ensure_case_storage_layout(settings, case, workspace)
    source_name = file.filename or "upload.nii.gz"
    stored_name = (
        case_named_upload(case.title, source_name)
        if name_after_case
        else _unique_upload_name_for_case(db, case, workspace, case_dir, source_name)
    )
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
        metadata={"source": "upload", "visible": visible},
        mime_type=mime_type,
    )


async def _store_case_dicom_uploads(
    db: Session,
    case: Case,
    workspace: Workspace,
    upload_files: list[UploadFile],
    relative_paths: list[PurePosixPath] | None = None,
) -> list[Artifact]:
    """Convert DICOM uploads to NIfTI files and register the resulting artifacts."""
    case_dir = ensure_case_storage_layout(settings, case, workspace)
    temporary_root = settings.fs_data_root / ".tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dicom-upload-", dir=temporary_root) as temp_root_name:
        temp_root = Path(temp_root_name)
        input_dir = temp_root / "input"
        output_dir = temp_root / "converted"
        raw_dir = temp_root / "raw"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        await _stage_dicom_sources(upload_files, input_dir, raw_dir, relative_paths)
        # No SQLite snapshot should span slow external conversion. The caller
        # retains its case reservation while other cases continue normally.
        db.commit()
        _run_dcm2niix(input_dir, output_dir)

        converted_paths = sorted(
            {path for pattern in ("*.nii", "*.nii.gz") for path in output_dir.rglob(pattern) if path.is_file()},
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
    relative_paths: list[PurePosixPath] | None = None,
    initialize_viewer: bool = True,
) -> tuple[list[Artifact], list[str], dict[str, object], list[dict[str, object]]]:
    """Route validated uploads to the direct-volume or DICOM conversion storage path."""
    direct_uploads = [upload for upload in upload_files if _is_direct_volume_upload(upload)]
    dicom_uploads = [upload for upload in upload_files if _is_dicom_source_upload(upload)]
    # An explicit path manifest denotes a folder/file-tree import. Store mixed
    # content as-is; a pure DICOM selection retains the conversion behavior.
    if relative_paths is not None and any(len(path.parts) > 1 for path in relative_paths) and len(dicom_uploads) != len(upload_files):
        return await _store_folder_or_generic_uploads(
            db, case, workspace, upload_files, relative_paths, initialize_viewer=initialize_viewer
        )
    if direct_uploads and (dicom_uploads or len(upload_files) > 1):
        fallback_paths = relative_paths or [_safe_relative_upload_path(_upload_filename(upload)) for upload in upload_files]
        return await _store_folder_or_generic_uploads(
            db, case, workspace, upload_files, fallback_paths, initialize_viewer=initialize_viewer
        )
    if direct_uploads:
        artifact = await _store_case_upload(
            db,
            case,
            workspace,
            direct_uploads[0],
            name_after_case=name_after_case,
            visible=initialize_viewer,
        )
        return (
            [artifact],
            [artifact.name],
            {"format": "volume", "scope": "file", "subject_ids": [], "file_count": 1, "artifact_counts": {"volume": 1}},
            (
                [{"artifact_id": artifact.id, "filename": artifact.relative_path, "type": "intensity", "visible": True}]
                if initialize_viewer
                else []
            ),
        )
    if dicom_uploads:
        dicom_paths = None
        if relative_paths is not None:
            _root_name, dicom_paths = _logical_tree_paths(relative_paths)
        artifacts = await _store_case_dicom_uploads(
            db,
            case,
            workspace,
            dicom_uploads,
            dicom_paths,
        )
        if not initialize_viewer:
            for artifact in artifacts:
                artifact.metadata_json = {**(artifact.metadata_json or {}), "visible": False}
        return (
            artifacts,
            [artifact.name for artifact in artifacts],
            {
                "format": "dicom",
                "scope": "series",
                "subject_ids": [],
                "file_count": len(upload_files),
                "artifact_counts": {"volume": len(artifacts)},
            },
            [],
        )
    fallback_paths = relative_paths or [_safe_relative_upload_path(_upload_filename(upload)) for upload in upload_files]
    return await _store_folder_or_generic_uploads(db, case, workspace, upload_files, fallback_paths, initialize_viewer=initialize_viewer)
