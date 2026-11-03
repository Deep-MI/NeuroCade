"""Focus real labels in nested/cropped outputs through the shared assistant harness."""

import numpy as np
import pytest
from api_service.assistant.tools.builder import AssistantToolBuilder
from api_service.assistant.tools.definition import ToolExecutionContext
from api_service.mcp_adapter import service
from api_service.runtime.gui_runtime import GuiRuntime
from fastapi import HTTPException
from nibabel.loadsave import save
from nibabel.nifti1 import Nifti1Image
from test_mcp_adapter import database as database

from backend_common.case_storage import case_storage_dir
from backend_common.db import Artifact, ArtifactKind
from backend_common.settings import get_settings


def setup_case(db):
    root = case_storage_dir(get_settings(), 'w', 'case-w')
    path = root / 'thalamus/mri/nuclei.nii.gz'
    path.parent.mkdir(parents=True)
    data = np.zeros((4, 5, 6), dtype=np.int16)
    data[1, 2, 3] = 8103
    data[2, 2, 3] = 8103
    # Cropped, permuted, scaled, translated grid: raw centroid 1.5,2,3 -> mm 6,24.5,29.25.
    affine = np.array([[0, -2, 0, 10], [3, 0, 0, 20], [0, 0, -.25, 30], [0, 0, 0, 1]])
    save(Nifti1Image(data, affine), path)
    db.add(Artifact(id='nuclei', case_id='case-w', workspace_id='w', name=path.name,
                    kind=ArtifactKind.volume, relative_path='thalamus/mri/nuclei.nii.gz',
                    metadata_json={'volume_role': 'segmentation', 'lut': 'freesurfer'}))
    db.commit()
    _, state = service.state_for(db, 'c', 'case-w')
    state['gui_state_override'] = {'layers': [{'id': 'viewer-layer', 'artifact_id': 'nuclei',
        'filename': path.name, 'type': 'segmentation', 'visible': True}]}
    runtime = GuiRuntime()
    builder = AssistantToolBuilder(runtime, settings=get_settings())
    tool = next(t for t in builder.build(state)[0] if t.name == 'gui_focus_label')
    return runtime, builder, state, tool


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', [{'layer_id': 'viewer-layer'}, {'segmentation_file': 'nuclei.nii.gz'},
                                      {'segmentation_file': '/case/thalamus/mri/nuclei.nii.gz'}])
async def test_nested_centroid_is_sent_as_world_coordinates(database, selector):
    with database() as db:
        runtime, builder, state, tool = setup_case(db)
        result = await tool.execute(ToolExecutionContext(call_id='focus'), {'label_query': '8103', **selector})
        assert not result.is_error
        assert 'thalamus/mri/nuclei.nii.gz' in result.content
        commands = runtime.gui_state_store.state_for_key(builder.gui_state_session_key(state))['commands']
        assert commands[-1]['payload'] == {'position': [6.0, 24.5, 29.25], 'coordinate_space': 'world'}


@pytest.mark.asyncio
async def test_missing_nucleus_does_not_focus_parent_or_queue(database):
    with database() as db:
        runtime, builder, state, tool = setup_case(db)
        result = await tool.execute(ToolExecutionContext(call_id='missing'), {'label_query': '49', 'layer_id': 'viewer-layer'})
        assert result.is_error
        assert 'not found in the segmentation' in result.content
        assert not runtime.gui_state_store.state_for_key(builder.gui_state_session_key(state)).get('commands')


@pytest.mark.asyncio
async def test_duplicate_basename_requires_exact_target(database):
    with database() as db:
        _, _, _, tool = setup_case(db)
        db.add(Artifact(id='other-nuclei', case_id='case-w', workspace_id='w', name='nuclei.nii.gz',
                        kind=ArtifactKind.volume, relative_path='other/mri/nuclei.nii.gz'))
        db.commit()
        with pytest.raises(HTTPException, match='Ambiguous'):
            await tool.execute(ToolExecutionContext(call_id='ambiguous'), {'label_query': '8103', 'segmentation_file': 'nuclei.nii.gz'})


@pytest.mark.asyncio
async def test_other_case_layer_reference_is_rejected(database):
    with database() as db:
        runtime, builder, state, tool = setup_case(db)
        db.get(Artifact, 'nuclei').case_id = 'case-other'
        db.get(Artifact, 'nuclei').workspace_id = 'other'
        db.commit()
        with pytest.raises(HTTPException, match='active case'):
            await tool.execute(ToolExecutionContext(call_id='wrong-case'), {'label_query': '8103', 'layer_id': 'viewer-layer'})
        assert not runtime.gui_state_store.state_for_key(builder.gui_state_session_key(state)).get('commands')


@pytest.mark.asyncio
async def test_exact_path_escape_is_rejected(database):
    with database() as db:
        _, _, _, tool = setup_case(db)
        result = await tool.execute(ToolExecutionContext(call_id='escape'), {'label_query': '8103', 'segmentation_file': '/case/../../outside.mgz'})
        assert result.is_error
        assert 'inside the active case' in result.content
