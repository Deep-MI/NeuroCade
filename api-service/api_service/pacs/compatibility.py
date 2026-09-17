"""Resolve PACS input policy from database artifacts, never writable sidecars."""
from contextlib import nullcontext

from nibabel.loadsave import load
from nibabel.spatialimages import SpatialImage

from api_service.pacs.outputs import fingerprint
from api_service.runtime import settings
from api_service.runtime_tools.errors import WorkflowInputError
from backend_common.case_storage import workspace_storage_dir
from backend_common.db import Artifact, ArtifactKind, SessionLocal, Workspace
from backend_common.storage import resolve_artifact_path


def validate_input(path, requirement, db=None):
    if not (requirement.modalities or requirement.dimensions or requirement.sequences):
        return
    if not path.is_relative_to(settings.fs_data_root.resolve()):
        return  # Not managed application storage (e.g. isolated catalog tooling).
    with (nullcontext(db) if db is not None else SessionLocal()) as db:
        workspace_id = None
        for workspace in db.query(Workspace).all():
            try:
                if path.is_relative_to(workspace_storage_dir(settings, workspace.id).resolve()):
                    workspace_id = workspace.id
                    break
            except FileNotFoundError:
                continue
        if workspace_id is None:
            raise ValueError("Input is outside managed workspace storage")
        records = db.query(Artifact).filter(Artifact.kind == ArtifactKind.volume,
                                          Artifact.workspace_id == workspace_id,
                                          Artifact.metadata_json["source"].as_string() == "pacs").all()
        if not records:
            return
        digest = fingerprint(path)
        matches = []
        exact = []
        for record in records:
            metadata = record.metadata_json or {}
            if resolve_artifact_path(record) == path:
                exact.append(metadata)
            elif metadata.get("sha256") == digest:
                matches.append(metadata)
        # Exact artifact wins. Copies must satisfy every same-workspace match.
        for metadata in exact or matches:
            if metadata.get("sha256") != digest:
                raise WorkflowInputError("pacs_provenance_invalid")
            if requirement.modalities and metadata.get("modality") not in requirement.modalities:
                raise WorkflowInputError("pacs_modality_incompatible")
            if requirement.dimensions:
                image = load(path)
                if not isinstance(image, SpatialImage) or len(image.shape) not in requirement.dimensions:
                    raise WorkflowInputError("pacs_dimensions_incompatible")
            if requirement.sequences and metadata.get("sequence") not in requirement.sequences:
                raise WorkflowInputError("pacs_sequence_incompatible")
