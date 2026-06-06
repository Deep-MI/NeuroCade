"""Provide shared backend sample seed utilities for NeuroCade."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from backend_common.case_events import record_case_event
from backend_common.case_storage import build_case_id, case_storage_dir, ensure_case_storage_layout
from backend_common.db import Artifact, ArtifactKind, Case, RoleEnum, User, Workspace, WorkspaceMembership
from backend_common.surface_artifacts import ANNOTATION_FILES, CURVATURE_FILES, SURFACE_FILES, classify_annotation_metadata, classify_curvature_metadata, classify_surface_metadata
from backend_common.storage import resolve_artifact_path
from backend_common.settings import ROOT_DIR, get_settings


settings = get_settings()
GENERATED_SAMPLE_CASE_ROOT = ROOT_DIR / "sample_case" / "FastSurfer_Rhineland_0000"
SAMPLE_CASE_ROOT = GENERATED_SAMPLE_CASE_ROOT
SAMPLE_CASE_TITLE = "sample-case"
SAMPLE_CASE_DESCRIPTION = "Repo-seeded FastSurfer demonstration case generated from the Rhineland T1w sample"
GLOBAL_SAMPLE_OWNER_ID = "sample-data-owner"
GLOBAL_SAMPLE_WORKSPACE_ID = "sample-data-workspace"

SAMPLE_VOLUME_METADATA: dict[str, dict[str, object]] = {
    "001.mgz": {"volume_role": "intensity", "visible": True},
    "orig.mgz": {"volume_role": "intensity", "visible": False},
    "orig_nu.mgz": {"volume_role": "intensity", "visible": False},
    "mask.mgz": {"volume_role": "segmentation", "lut": "binary", "visible": False},
    "aseg.auto_noCCseg.mgz": {"volume_role": "segmentation", "lut": "freesurfer", "visible": False},
    "aparc.DKTatlas+aseg.deep.mgz": {"volume_role": "segmentation", "lut": "freesurfer", "visible": True},
    "aparc.DKTatlas+aseg.deep.withCC.mgz": {"volume_role": "segmentation", "lut": "freesurfer", "visible": False},
    "wmparc.DKTatlas.mapped.mgz": {"volume_role": "segmentation", "lut": "freesurfer", "visible": False},
}
SAMPLE_VISIBLE_SURFACES = {"lh.pial", "rh.pial"}

MIME_TYPES: dict[str, str] = {
    ".gz": "application/octet-stream",
    ".png": "image/png",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".md": "text/markdown",
}


def sample_case_id_for_workspace(workspace_id: str) -> str:
    """Build the stable seeded sample case ID for a workspace."""
    return build_case_id(workspace_id, SAMPLE_CASE_TITLE)


def _artifact_kind_for_path(path: Path) -> ArtifactKind:
    """Classify a sample file as a volume, derived artifact, or report."""
    if path.name in SURFACE_FILES or path.name in CURVATURE_FILES or path.name in ANNOTATION_FILES:
        return ArtifactKind.derived
    if path.name.endswith(".nii.gz") or path.name.endswith(".mgz"):
        return ArtifactKind.volume
    return ArtifactKind.report


def _metadata_for_path(path: Path) -> dict[str, object]:
    """Return display and classification metadata for a seeded artifact."""
    metadata = {"source": "sample-case-seed", **SAMPLE_VOLUME_METADATA.get(path.name, {})}
    if path.name in SURFACE_FILES:
        metadata.update(classify_surface_metadata(path.name))
        metadata["visible"] = path.name in SAMPLE_VISIBLE_SURFACES
    elif path.name in CURVATURE_FILES:
        metadata.update(classify_curvature_metadata(path.name))
    elif path.name in ANNOTATION_FILES:
        metadata.update(classify_annotation_metadata(path.name))
    return metadata


def _mime_type_for_path(path: Path) -> str:
    """Infer the MIME type stored for a sample artifact path."""
    if path.suffix == ".gz" and path.name.endswith(".nii.gz"):
        return MIME_TYPES[".gz"]
    return MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _resolve_sample_case_root() -> Path:
    """Return the configured source directory for seeded sample files."""
    return SAMPLE_CASE_ROOT


def _sample_case_source_files(sample_case_root: Path) -> list[Path]:
    """List all regular files beneath the sample case source directory."""
    return sorted(path for path in sample_case_root.rglob("*") if path.is_file())


def _artifact_exists(artifact: Artifact) -> bool:
    """Check whether an artifact path resolves inside storage and exists."""
    try:
        candidate = (settings.fs_data_root / artifact.relative_path).resolve()
        root = settings.fs_data_root.resolve()
    except OSError:
        return False
    if root not in candidate.parents and candidate != root:
        return False
    return candidate.exists()


def _sample_case_requires_refresh(
    db: Session,
    case: Case,
    workspace: Workspace,
    source_files: list[Path],
) -> bool:
    """Return whether stored sample files or artifact rows are stale."""
    sample_case_root = _resolve_sample_case_root()
    expected_paths = {str(path.relative_to(sample_case_root)) for path in source_files}
    current_case_dir = case_storage_dir(settings, workspace.id, case.id)
    current_paths: set[str] = set()
    if current_case_dir.exists():
        current_paths = {str(path.relative_to(current_case_dir)) for path in current_case_dir.rglob("*") if path.is_file()}

    artifacts = db.query(Artifact).filter(Artifact.case_id == case.id).all()
    current_artifacts = [artifact for artifact in artifacts if _artifact_exists(artifact)]
    try:
        artifact_paths = [
            str(resolve_artifact_path(artifact.relative_path).resolve().relative_to(current_case_dir.resolve()))
            for artifact in current_artifacts
        ]
    except ValueError:
        return True

    if not expected_paths.issubset(current_paths):
        return True

    artifact_map: dict[str, Artifact] = {}
    expected_artifact_paths: set[str] = set()
    for artifact_path, artifact in zip(artifact_paths, current_artifacts):
        if artifact_path not in expected_paths:
            continue
        if artifact_path in expected_artifact_paths:
            return True
        expected_artifact_paths.add(artifact_path)
        artifact_map[artifact_path] = artifact
    if expected_artifact_paths != expected_paths:
        return True

    for source_path in source_files:
        artifact = artifact_map.get(str(source_path.relative_to(sample_case_root)))
        if artifact is None:
            return True
        if artifact.kind != _artifact_kind_for_path(source_path):
            return True
        if artifact.mime_type != _mime_type_for_path(source_path):
            return True
        if artifact.size_bytes != source_path.stat().st_size:
            return True
        if dict(artifact.metadata_json or {}) != _metadata_for_path(source_path):
            return True
    return False


def ensure_sample_case(db: Session, user: User) -> Case | None:
    """Create or refresh the per-user sample case and its artifacts."""
    sample_case_root = _resolve_sample_case_root()
    if not sample_case_root.exists():
        return None
    sample_files = _sample_case_source_files(sample_case_root)
    if not sample_files:
        return None

    workspace = (
        db.query(Workspace)
        .filter(Workspace.owner_user_id == user.id, Workspace.is_default.is_(True))
        .one_or_none()
    )
    materialize_sample_files = False

    if workspace is None:
        return None

    case_id = sample_case_id_for_workspace(workspace.id)
    case = db.get(Case, case_id)
    if case is None:
        case = (
            db.query(Case)
            .filter(
                Case.workspace_id == workspace.id,
                Case.owner_user_id == user.id,
                Case.description == SAMPLE_CASE_DESCRIPTION,
            )
            .order_by(Case.created_at.asc(), Case.id.asc())
            .first()
        )

    if case is None:
        case = Case(
            id=case_id,
            workspace_id=workspace.id,
            owner_user_id=user.id,
            title=SAMPLE_CASE_TITLE,
            description=SAMPLE_CASE_DESCRIPTION,
        )
        db.add(case)
        db.flush()
        materialize_sample_files = True
    else:
        existing_workspace = db.get(Workspace, case.workspace_id) or workspace
        if _sample_case_requires_refresh(db, case, existing_workspace, sample_files):
            materialize_sample_files = True
        if case.workspace_id != workspace.id:
            materialize_sample_files = True
        case.workspace_id = workspace.id
        case.description = SAMPLE_CASE_DESCRIPTION

    workspace_membership = (
        db.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id, WorkspaceMembership.user_id == user.id)
        .one_or_none()
    )
    if workspace_membership is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=RoleEnum.owner,
                granted_by_user_id=user.id,
            )
        )

    if materialize_sample_files:
        target_dir = ensure_case_storage_layout(db, settings, case, workspace)

        for source_path in sample_files:
            filename = source_path.name
            relative_source_path = source_path.relative_to(sample_case_root)
            target_path = target_dir / relative_source_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            relative_path = str(target_path.resolve().relative_to(settings.fs_data_root.resolve()))
            matching_artifacts = (
                db.query(Artifact)
                .filter(Artifact.case_id == case.id, Artifact.relative_path == relative_path)
                .order_by(Artifact.created_at.asc(), Artifact.id.asc())
                .all()
            )
            existing_artifact = matching_artifacts[0] if matching_artifacts else None
            for duplicate in matching_artifacts[1:]:
                db.delete(duplicate)
            artifact_kind = _artifact_kind_for_path(source_path)
            artifact_mime_type = _mime_type_for_path(source_path)
            artifact_metadata = _metadata_for_path(source_path)
            if existing_artifact is None:
                db.add(
                    Artifact(
                        case_id=case.id,
                        workspace_id=workspace.id,
                        kind=artifact_kind,
                        name=filename,
                        relative_path=relative_path,
                        mime_type=artifact_mime_type,
                        size_bytes=target_path.stat().st_size,
                        metadata_json=artifact_metadata,
                    )
                )
            else:
                existing_artifact.workspace_id = workspace.id
                existing_artifact.kind = artifact_kind
                existing_artifact.name = filename
                existing_artifact.relative_path = relative_path
                existing_artifact.mime_type = artifact_mime_type
                existing_artifact.size_bytes = target_path.stat().st_size
                existing_artifact.metadata_json = artifact_metadata

        record_case_event(
            db,
            case,
            "case.seeded",
            user_id=user.id,
            details={"source": "sample_case", "file_count": len(sample_files)},
        )

    db.flush()
    return case


def ensure_global_sample_workspace_membership(db: Session, user: User) -> Workspace | None:
    """Ensure the user can access the shared sample data workspace."""
    sample_owner = db.get(User, GLOBAL_SAMPLE_OWNER_ID)
    if sample_owner is None:
        sample_owner = User(
            id=GLOBAL_SAMPLE_OWNER_ID,
            external_auth_id=GLOBAL_SAMPLE_OWNER_ID,
            email="sample-data@neurocade.local",
            full_name="NeuroCade Sample Data",
        )
        db.add(sample_owner)
    db.flush()
    workspace = db.get(Workspace, GLOBAL_SAMPLE_WORKSPACE_ID)
    if workspace is None:
        workspace = Workspace(
            id=GLOBAL_SAMPLE_WORKSPACE_ID,
            owner_user_id=sample_owner.id,
            name=GLOBAL_SAMPLE_WORKSPACE_ID,
            description="Sample/de-identified NeuroCade workspace for public exploration.",
            kind="sample",
            is_default=True,
        )
        db.add(workspace)
        db.flush()
    owner_membership = (
        db.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id, WorkspaceMembership.user_id == sample_owner.id)
        .one_or_none()
    )
    if owner_membership is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=sample_owner.id,
                role=RoleEnum.owner,
                granted_by_user_id=sample_owner.id,
            )
        )
    user_membership = (
        db.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id, WorkspaceMembership.user_id == user.id)
        .one_or_none()
    )
    if user_membership is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=RoleEnum.user,
                granted_by_user_id=sample_owner.id,
            )
        )
    ensure_sample_case(db, sample_owner)
    db.flush()
    return workspace
