"""Provide shared backend scan utilities for NeuroCade."""

import os
from pathlib import Path

from backend_common.artifact_upsert import insert_artifact_if_missing
from backend_common.case_events import record_case_event
from backend_common.case_storage import ensure_case_storage_layout, list_case_upload_files
from backend_common.db import (
    Artifact,
    ArtifactKind,
    Case,
    RoleEnum,
    Workspace,
    WorkspaceMembership,
)
from backend_common.surface_artifacts import (
    ANNOTATION_FILES,
    CURVATURE_FILES,
    SURFACE_FILES,
    classify_annotation_metadata,
    classify_curvature_metadata,
    classify_surface_metadata,
)

ALLOWED_FILES = {
    "001.mgz",
    "inpainting_original_image.mgz",
    "orig.mgz",
    "mask.mgz",
    "orig_nu.mgz",
    "aparc.DKTatlas+aseg.deep.mgz",
    "aseg.auto_noCCseg.mgz",
    "cerebellum.CerebNet.nii.gz",
    "hypothalamus.HypVINN.nii.gz",
    "hypothalamus_mask.HypVINN.nii.gz",
    "hypothalamus_bin.nii.gz",
    "aparc.DKTatlas+aseg.deep.withCC.mgz",
    "wmparc.DKTatlas.mapped.mgz",
}
VOLUME_SUFFIXES = (".mgz", ".mgh", ".nii", ".nii.gz")
def classify_volume_metadata(filename: str) -> dict:
    """Infer volume role and LUT metadata from a volume filename."""
    lower = filename.lower()
    is_seg = any(token in lower for token in ("aseg", "aparc", "seg", "mask", "cereb", "wmparc", "hypothal"))
    is_binary = any(token in lower for token in ("mask", "brainmask")) or "_bin" in lower
    metadata = {
        "volume_role": "segmentation" if is_seg else "intensity",
    }
    if is_binary:
        metadata["lut"] = "binary"
    elif is_seg:
        metadata["lut"] = "freesurfer"
    return metadata


def _ensure_workspace_membership(db, workspace_id: str, user_id: str) -> None:
    """Create an owner membership for a workspace when one is missing."""
    membership = (
        db.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.user_id == user_id)
        .one_or_none()
    )
    if membership is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace_id,
                user_id=user_id,
                role=RoleEnum.owner,
                granted_by_user_id=user_id,
            )
        )


def index_case_files_from_storage(
    db,
    settings,
    user_id: str,
    case_id: str,
    workspace_id: str,
    *,
    case_title: str | None = None,
    preferred_upload_name: str | None = None,
) -> None:
    """Register a case's indexed storage files in the database."""
    normalized_case_title = (case_title or case_id).strip()
    case = db.get(Case, case_id)
    resolved_workspace_id = case.workspace_id if case is not None else workspace_id
    workspace = db.get(Workspace, resolved_workspace_id)
    created_case = False

    if workspace is None:
        raise ValueError(f"Workspace {resolved_workspace_id} not found for case indexing")

    if case is None:
        case = Case(
            id=case_id,
            workspace_id=workspace.id,
            owner_user_id=user_id,
            title=normalized_case_title,
        )
        db.add(case)
        db.flush()
        created_case = True
    else:
        case.workspace_id = workspace.id
        if case_title:
            case.title = normalized_case_title

    _ensure_workspace_membership(db, workspace.id, case.owner_user_id)

    case_dir = ensure_case_storage_layout(
        db,
        settings,
        case,
        workspace,
        preferred_upload_name=preferred_upload_name,
    )

    if created_case:
        status = "uploaded"
        status_path = case_dir / "status.json"
        if status_path.exists():
            raw = status_path.read_text(encoding="utf-8")
            if '"finished"' in raw:
                status = "completed"
            elif '"running"' in raw or '"queued"' in raw or '"starting"' in raw:
                status = "running"
        record_case_event(
            db,
            case,
            "case.indexed",
            user_id=user_id,
            details={"status": status},
        )

    existing_paths = {
        relative_path
        for (relative_path,) in db.query(Artifact.relative_path).filter(Artifact.case_id == case.id).all()
    }

    for upload_file in list_case_upload_files(settings, workspace, case):
        rel_path = str(upload_file.resolve().relative_to(settings.fs_data_root.resolve()))
        if rel_path in existing_paths:
            continue
        insert_artifact_if_missing(
            db,
            {
                "case_id": case.id,
                "workspace_id": workspace.id,
                "kind": ArtifactKind.volume,
                "name": upload_file.name,
                "relative_path": rel_path,
                "mime_type": "application/octet-stream",
                "size_bytes": upload_file.stat().st_size,
                "metadata_json": {
                    "source": "filesystem-index",
                    "volume_role": "intensity",
                    "preferred_upload_name": upload_file.name == preferred_upload_name if preferred_upload_name else False,
                },
            },
            case_scoped=True,
        )
        existing_paths.add(rel_path)

    for root, _dirs, files in os.walk(case_dir):
        for filename in files:
            artifact_path = Path(root) / filename
            if artifact_path.is_symlink():
                continue
            if artifact_path.parent == case_dir and filename not in {"status.json", "subject.txt"}:
                continue
            is_volume = filename.endswith(VOLUME_SUFFIXES)
            is_surface = filename in SURFACE_FILES and Path(root).name == "surf"
            is_curvature = filename in CURVATURE_FILES and Path(root).name == "surf"
            is_annotation = filename in ANNOTATION_FILES and Path(root).name in {"label", case_dir.name}
            if filename not in ALLOWED_FILES and not is_volume and not is_surface and not is_curvature and not is_annotation and not filename.endswith((".log", ".json", ".txt")):
                continue
            resolved_artifact_path = artifact_path.resolve()
            resolved_case_dir = case_dir.resolve()
            if resolved_case_dir not in resolved_artifact_path.parents and resolved_artifact_path != resolved_case_dir:
                continue
            rel_path = str(resolved_artifact_path.relative_to(settings.fs_data_root.resolve()))
            if rel_path in existing_paths:
                continue
            kind = ArtifactKind.derived if is_surface or is_curvature or is_annotation else ArtifactKind.volume if is_volume else ArtifactKind.log
            if filename.endswith(".json") or filename.endswith(".txt"):
                kind = ArtifactKind.report
            insert_artifact_if_missing(
                db,
                {
                    "case_id": case.id,
                    "workspace_id": workspace.id,
                    "kind": kind,
                    "name": filename,
                    "relative_path": rel_path,
                    "mime_type": "application/octet-stream",
                    "size_bytes": artifact_path.stat().st_size,
                    "metadata_json": {
                        "source": "filesystem-index",
                        **(
                            classify_surface_metadata(filename)
                            if is_surface
                            else classify_curvature_metadata(filename)
                            if is_curvature
                            else classify_annotation_metadata(filename)
                            if is_annotation
                            else classify_volume_metadata(filename)
                            if kind == ArtifactKind.volume
                            else {}
                        ),
                    },
                },
                case_scoped=True,
            )
            existing_paths.add(rel_path)
