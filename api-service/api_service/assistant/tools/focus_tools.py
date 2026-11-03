"""Resolve viewer selections to authorized segmentation files before label focus."""
from pathlib import PurePosixPath

from fastapi import HTTPException

from api_service.artifacts.service import authorize_artifact_for_user
from api_service.helpers import get_case_for_user
from backend_common.db import Artifact


def resolve_focus_arguments(state: dict, arguments: dict, gui_state: dict) -> dict:
    db, context = state['db'], state['context']
    case, _, _, _ = get_case_for_user(db, state['case_id'], context.user.id, workspace_id=state['workspace_id'])
    requested = next((arguments[key] for key in ('segmentation_file', 'segmentation_volume', 'segmentation_path', 'segmentation_map')
                      if arguments.get(key)), None)
    layer_id = arguments.get('layer_id')
    artifact = None
    if layer_id:
        if requested:
            raise HTTPException(400, 'Choose layer_id or segmentation_file, not both.')
        layers = [layer for layer in gui_state.get('layers', []) if layer.get('id') == layer_id]
        if len(layers) != 1 or layers[0].get('type') != 'segmentation':
            raise HTTPException(400, 'layer_id must identify one loaded segmentation from gui_list_layers.')
        artifact_id = layers[0].get('artifact_id')
        if not artifact_id:
            raise HTTPException(400, 'Layer has no artifact reference; pass its exact /case/... segmentation_file.')
        artifact = authorize_artifact_for_user(db, context, artifact_id)
    elif isinstance(requested, str) and '/' not in requested:
        matches = db.query(Artifact).filter(Artifact.case_id == case.id, Artifact.name == requested).all()
        if len(matches) > 1:
            raise HTTPException(400, 'Ambiguous segmentation filename; use layer_id or the exact /case/... path.')
        artifact = matches[0] if matches else None
    if artifact is None:
        return arguments
    if artifact.case_id != case.id or artifact.workspace_id != case.workspace_id:
        raise HTTPException(404, 'Segmentation does not belong to the active case.')
    if (artifact.metadata_json or {}).get('source') == 'linked':
        raise HTTPException(400, 'Label focus requires a managed case segmentation.')
    relative = PurePosixPath(artifact.relative_path)
    if relative.is_absolute() or '..' in relative.parts:
        raise HTTPException(400, 'Invalid segmentation artifact path.')
    return {**arguments, 'segmentation_file': '/case/' + relative.as_posix()}
