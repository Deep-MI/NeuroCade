"""Register an existing managed case file without processing or copying it."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from nibabel.filebasedimages import ImageFileError
from nibabel.freesurfer.io import read_geometry
from nibabel.loadsave import load
from nibabel.spatialimages import SpatialImage
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from api_service.artifacts.service import serialize_artifact
from api_service.cases.files import resolve_case_file
from api_service.helpers import get_case_for_user
from api_service.policies import require_case_write
from api_service.runtime_tools.load_handler import _surface_parts
from backend_common.artifact_classification import VOLUME_SUFFIXES, classify_artifact
from backend_common.artifact_upsert import insert_artifact_if_missing
from backend_common.auth import AuthContext


class RegisterCaseFile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    file_path: str = Field(description='Existing managed /case/... file; use case_file_tree to find its exact path.')
    layer_type: Literal['intensity', 'segmentation', 'surface']
    lut: Literal['freesurfer', 'binary'] | None = None

    @model_validator(mode='after')
    def validate_layer(self):
        if self.layer_type == 'segmentation' and self.lut is None:
            raise ValueError('Segmentation registration requires an explicit lut.')
        if self.layer_type != 'segmentation' and self.lut is not None:
            raise ValueError('lut is only supported for segmentation layers.')
        if not self.file_path.startswith('/case/'):
            raise ValueError('Use an active-case /case/... path.')
        return self


def register_case_file(db: Session, context: AuthContext, *, workspace_id: str,
                       case_id: str, request: RegisterCaseFile) -> dict:
    case, _, role, _ = get_case_for_user(db, case_id, context.user.id, workspace_id=workspace_id)
    require_case_write(role)
    relative = request.file_path.removeprefix('/case/')
    entry = resolve_case_file(db, context, case.id, 'managed', relative)
    path = entry.managed_path
    if entry.kind != 'file' or path is None:
        raise HTTPException(400, 'Choose an existing managed file, not a directory or linked source.')
    try:
        if request.layer_type == 'surface':
            if _surface_parts(path.name) is None:
                raise ValueError('Choose a supported lh/rh FreeSurfer surface mesh.')
            read_geometry(path)
            declared_type = 'surface'
        else:
            if not path.name.lower().endswith(VOLUME_SUFFIXES):
                raise ValueError('Choose an MGZ, MGH, NIfTI or compressed NIfTI volume.')
            volume = load(path)
            if not isinstance(volume, SpatialImage) or len(volume.shape) not in (3, 4):
                raise ValueError('Expected a spatial MRI volume.')
            if request.layer_type == 'segmentation' and len(volume.shape) != 3:
                raise ValueError('Segmentation must be a 3D label volume.')
            declared_type = 'segmentation_volume' if request.layer_type == 'segmentation' else 'intensity_volume'
    except (OSError, ValueError, ImageFileError) as exc:
        raise HTTPException(400, f'Cannot register file: {exc}') from exc
    metadata: dict[str, object] = {'source': 'existing-case-file', 'output_type': declared_type}
    if request.lut:
        metadata['lut'] = request.lut
    classification = classify_artifact(path, declared_type=declared_type, metadata=metadata)
    assert classification is not None
    artifact = insert_artifact_if_missing(db, {
        'case_id': case.id, 'workspace_id': case.workspace_id, 'kind': classification.kind,
        'name': path.name, 'relative_path': relative, 'mime_type': classification.mime_type,
        'size_bytes': path.stat().st_size, 'metadata_json': classification.metadata,
    }, case_scoped=True)
    assert artifact is not None
    # Retain producer/run/display metadata; explicit layer semantics replace stale guesses.
    previous = dict(artifact.metadata_json or {})
    for key in ('volume_role', 'layer_role', 'lut', 'hemisphere', 'surface_role'):
        previous.pop(key, None)
    artifact.metadata_json = {**previous, **classification.metadata,
                              'source': previous.get('source', 'existing-case-file')}
    artifact.kind = classification.kind
    artifact.mime_type = classification.mime_type
    artifact.size_bytes = path.stat().st_size
    db.flush()
    return {'status': 'registered', 'artifact': serialize_artifact(artifact).model_dump(mode='json'),
            'file_path': request.file_path, 'loaded': False,
            'validation': 'File format/header checked; layer type and LUT explicitly supplied. Scientific correctness not verified.',
            'next_step': {'tool': 'gui_load_layer', 'arguments': {'file_path': request.file_path}}}
