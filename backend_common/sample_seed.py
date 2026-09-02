"""Provide shared backend sample seed utilities for NeuroCade."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from backend_common.artifact_classification import ArtifactClassification, classify_artifact
from backend_common.case_events import record_case_event
from backend_common.case_storage import ensure_case_storage_layout, ensure_workspace_storage_layout
from backend_common.db import Artifact, ArtifactKind, Case, RoleEnum, User, Workspace, WorkspaceMembership
from backend_common.settings import ROOT_DIR, get_settings
from backend_common.surface_artifacts import SURFACE_FILES

settings = get_settings()
SAMPLE_CASE_ROOT = ROOT_DIR / "sample_case" / "FastSurfer_Rhineland_0000"
SAMPLE_CASE_TITLE = "sample-case"
SAMPLE_CASE_DESCRIPTION = "Repo-seeded FastSurfer demonstration case generated from the Rhineland T1w sample"
GLOBAL_SAMPLE_OWNER_ID = "sample-data-owner"
GLOBAL_SAMPLE_WORKSPACE_ID = str(uuid5(NAMESPACE_URL, "neurocade:sample-data-workspace"))

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


def sample_case_id_for_workspace(workspace_id: str) -> str:
    """Build the stable seeded sample case ID for a workspace."""
    return str(uuid5(NAMESPACE_URL, f"neurocade:{workspace_id}:sample-case"))


def _sample_artifact_classification(path: Path) -> ArtifactClassification:
    """Classify one seeded file with its sample-specific display metadata."""
    metadata = {"source": "sample-case-seed", **SAMPLE_VOLUME_METADATA.get(path.name, {})}
    if path.name in SURFACE_FILES:
        metadata["visible"] = path.name in SAMPLE_VISIBLE_SURFACES
    classification = classify_artifact(
        path,
        metadata=metadata,
        fallback_kind=ArtifactKind.report,
    )
    if classification is None:
        raise ValueError(f"Could not classify sample artifact: {path}")
    return classification


def _sample_case_source_files(sample_case_root: Path) -> list[Path]:
    """List all regular files beneath the sample case source directory."""
    return sorted(path for path in sample_case_root.rglob("*") if path.is_file())


def ensure_sample_case(db: Session, user: User) -> Case | None:
    """Import the sample case once, then leave it under normal case ownership."""
    workspace = db.query(Workspace).filter(Workspace.owner_user_id == user.id, Workspace.is_default.is_(True)).one_or_none()
    if workspace is None:
        return None

    case_id = sample_case_id_for_workspace(workspace.id)
    case = db.get(Case, case_id)
    if case is not None:
        return case

    sample_case_root = SAMPLE_CASE_ROOT
    if not sample_case_root.exists():
        return None
    sample_files = _sample_case_source_files(sample_case_root)
    if not sample_files:
        return None

    ensure_workspace_storage_layout(settings, workspace)
    case = Case(
        id=case_id,
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title=SAMPLE_CASE_TITLE,
        description=SAMPLE_CASE_DESCRIPTION,
    )
    db.add(case)
    db.flush()

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

    target_dir = ensure_case_storage_layout(settings, case, workspace)

    for source_path in sample_files:
        filename = source_path.name
        relative_source_path = source_path.relative_to(sample_case_root)
        target_path = target_dir / relative_source_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        relative_path = str(target_path.resolve().relative_to(target_dir.resolve()))
        classification = _sample_artifact_classification(source_path)
        db.add(
            Artifact(
                case_id=case.id,
                workspace_id=workspace.id,
                kind=classification.kind,
                name=filename,
                relative_path=relative_path,
                mime_type=classification.mime_type,
                size_bytes=target_path.stat().st_size,
                metadata_json=classification.metadata,
            )
        )

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
            name="sample-data-workspace",
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
