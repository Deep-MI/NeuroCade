"""Register untracked results through the shared tool and resolve a typed viewer load."""
import hashlib

import numpy as np
import pytest
from api_service.assistant.tools.builder import AssistantToolBuilder
from api_service.assistant.tools.definition import ToolExecutionContext, ToolRisk
from api_service.gui_state import build_gui_state_session_key
from api_service.mcp_adapter import service
from api_service.runtime.gui_runtime import GuiRuntime, gui_runtime
from api_service.viewer_resources import resolve_load_layer_command
from fastapi import HTTPException
from nibabel.freesurfer.io import write_geometry
from nibabel.loadsave import save
from nibabel.nifti1 import Nifti1Image
from pydantic import ValidationError
from test_mcp_adapter import database as database

from backend_common.case_storage import case_storage_dir
from backend_common.db import Artifact, McpClient, WorkspaceMembership
from backend_common.settings import get_settings


def fixture_file():
    root = case_storage_dir(get_settings(), 'w', 'case-w')
    path = root / 'results with spaces' / 'ThalamicNuclei.nii.gz'
    path.parent.mkdir()
    save(Nifti1Image(np.arange(27, dtype=np.int16).reshape(3, 3, 3), np.eye(4)), path)
    return path


def load_tool(state):
    return next(tool for tool in AssistantToolBuilder(GuiRuntime(), settings=get_settings()).build(state)[0]
                if tool.name == 'gui_load_layer')


async def register(db, **overrides):
    _, state = service.state_for(db, 'c', 'case-w')
    tool = load_tool(state)
    assert tool.risk == ToolRisk.gui
    # Close this test's lookup snapshot, as the shared invocation pipeline does.
    db.commit()
    return await tool.execute(ToolExecutionContext(call_id='register'), {
        'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
        'layer_type': 'segmentation', 'lut': 'freesurfer', **overrides,
    })


@pytest.mark.asyncio
async def test_registration_load_and_idempotency_preserve_file_and_provenance(database):
    path = fixture_file()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with database() as db:
        first = await register(db)
        artifact_id = first.details['artifact']['id']
        assert first.details['loaded'] is False
        artifact = db.get(Artifact, artifact_id)
        artifact.metadata_json = {**artifact.metadata_json, 'source': 'workflow-output', 'run_id': 'original-run'}
        db.commit()
        second = await register(db)
        assert second.details['artifact']['id'] == artifact_id
        assert second.details['artifact']['metadata']['run_id'] == 'original-run'
        assert db.query(Artifact).count() == 1
        _, state = service.state_for(db, 'c', 'case-w')
        output_path = path.relative_to(get_settings().outputs_dir).as_posix()
        command = resolve_load_layer_command(db, state['context'], {
            'resource': {'kind': 'output', 'path': 'outputs/' + output_path}, 'type': 'intensity',
        })
        assert command['artifact_id'] == artifact_id
        assert command['type'] == 'segmentation'
        assert command['lut'] == 'freesurfer'
        assert command['download_path'].endswith('/download')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
async def test_explicit_reclassification_clears_stale_lut(database):
    fixture_file()
    with database() as db:
        first = await register(db)
        second = await register(db, layer_type='intensity', lut=None)
        assert second.details['artifact']['id'] == first.details['artifact']['id']
        metadata = second.details['artifact']['metadata']
        assert metadata['volume_role'] == 'intensity'
        assert metadata['output_type'] == 'intensity_volume'
        assert 'lut' not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/case/../outside.nii.gz', '/tmp/scan.nii.gz', '/case/missing.nii.gz', '/case/results with spaces'])
async def test_bad_paths_do_not_register(database, path):
    fixture_file()
    with database() as db:
        with pytest.raises((HTTPException, ValidationError)):
            await register(db, file_path=path)
        assert db.query(Artifact).count() == 0


@pytest.mark.asyncio
async def test_symlinks_and_unreadable_volumes_rejected(database):
    path = fixture_file()
    link = path.parent / 'alias.nii.gz'
    link.symlink_to(path)
    with database() as db:
        with pytest.raises(HTTPException):
            await register(db, file_path='/case/results with spaces/alias.nii.gz')
        path.write_text('not a volume')
        with pytest.raises(HTTPException):
            await register(db)
        assert db.query(Artifact).count() == 0


@pytest.mark.asyncio
async def test_registration_requires_membership_and_explicit_lut(database):
    fixture_file()
    with database() as db:
        with pytest.raises(ValidationError):
            await register(db, lut=None)
        _, state = service.state_for(db, 'c', 'case-w')
        db.query(WorkspaceMembership).filter_by(workspace_id='w', user_id='u').delete()
        db.commit()
        with pytest.raises(HTTPException):
            await load_tool(state).execute(ToolExecutionContext(call_id='denied'), {
                'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
                'layer_type': 'segmentation', 'lut': 'freesurfer',
            })
        assert db.query(Artifact).count() == 0


@pytest.mark.asyncio
async def test_surface_registration(database):
    root = case_storage_dir(get_settings(), 'w', 'case-w')
    (root / 'surf').mkdir()
    write_geometry(root / 'surf/lh.pial', np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]]), np.array([[0, 1, 2]]))
    with database() as db:
        result = await register(db, file_path='/case/surf/lh.pial', layer_type='surface', lut=None)
        assert result.details['artifact']['metadata']['layer_role'] == 'surface'


@pytest.mark.asyncio
async def test_mcp_read_only_client_cannot_register(database):
    fixture_file()
    with database() as db:
        db.get(McpClient, 'c').access = 'read'
        db.commit()
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, 'c', 'gui_load_layer', {
                'case_id': 'case-w', 'arguments': {'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
                                                 'layer_type': 'segmentation', 'lut': 'freesurfer'},
                'idempotency_key': 'denied',
            })
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_shared_load_registers_and_queues_without_separate_approval_or_run(database):
    from backend_common.db import Run

    fixture_file()
    key = build_gui_state_session_key(user_id='u', workspace_id='w', case_id='case-w', gui_session_id='registration-viewer')
    gui_runtime.sync_gui_state({'workspace_id': 'w', 'case_id': 'case-w'}, gui_state_key=key)
    payload = {'case_id': 'case-w', 'gui_session_id': 'registration-viewer', 'arguments': {
        'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
        'layer_type': 'segmentation', 'lut': 'freesurfer'}}
    with database() as db:
        result = await service.invoke(db, 'c', 'gui_load_layer', payload)
        assert result['status'] == 'succeeded', result
        assert not result['result']['is_error'], result
        assert db.query(Artifact).count() == 1
        assert db.query(Run).count() == 0
        commands = gui_runtime.gui_state_store.state_for_key(key)['commands']
        assert commands[-1]['type'] == 'load_layer'
        assert commands[-1]['payload']['type'] == 'segmentation'
        assert commands[-1]['payload']['lut'] == 'freesurfer'
        with pytest.raises(HTTPException):
            await service.invoke(db, 'c', 'gui_load_layer', {**payload, 'case_id': 'case-other'})


@pytest.mark.asyncio
async def test_existing_segmentation_metadata_survives_plain_load(database):
    fixture_file()
    with database() as db:
        first = await register(db)
        _, state = service.state_for(db, 'c', 'case-w')
        db.commit()
        result = await load_tool(state).execute(ToolExecutionContext(call_id='load'), {
            'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
        })
        assert result.details['artifact']['id'] == first.details['artifact']['id']
        assert result.details['artifact']['metadata']['volume_role'] == 'segmentation'
        assert result.details['artifact']['metadata']['lut'] == 'freesurfer'


@pytest.mark.asyncio
async def test_untracked_volume_without_type_does_not_register_or_queue(database):
    fixture_file()
    runtime = GuiRuntime()
    with database() as db:
        _, state = service.state_for(db, 'c', 'case-w')
        db.commit()
        builder = AssistantToolBuilder(runtime, settings=get_settings())
        definitions = {tool.name: tool for tool in builder.build(state)[0]}
        assert 'register_case_file' not in definitions
        with pytest.raises(HTTPException, match='supply layer_type'):
            await definitions['gui_load_layer'].execute(ToolExecutionContext(call_id='bad'), {
                'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
            })
        assert db.query(Artifact).count() == 0
        assert not runtime.gui_state_store.state_for_key(builder.gui_state_session_key(state)).get('commands')


@pytest.mark.asyncio
async def test_registration_success_does_not_hide_gui_queue_failure(database, monkeypatch):
    fixture_file()
    runtime = GuiRuntime()
    monkeypatch.setattr(runtime, 'call_tool', lambda *args, **kwargs: 'Error: viewer unavailable')
    with database() as db:
        _, state = service.state_for(db, 'c', 'case-w')
        db.commit()
        tool = next(t for t in AssistantToolBuilder(runtime, settings=get_settings()).build(state)[0] if t.name == 'gui_load_layer')
        result = await tool.execute(ToolExecutionContext(call_id='load'), {
            'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
            'layer_type': 'segmentation', 'lut': 'freesurfer',
        })
        assert result.is_error
        assert result.details['status'] == 'registered'
        assert result.details['loaded'] is False
        assert db.query(Artifact).count() == 1
