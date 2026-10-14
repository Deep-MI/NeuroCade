"""Register untracked results through the shared tool and resolve a typed viewer load."""
import hashlib

import numpy as np
import pytest
from api_service.assistant.tools.artifact_tools import artifact_tools
from api_service.assistant.tools.definition import ToolExecutionContext, ToolRisk
from api_service.mcp_adapter import service
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


async def register(db, **overrides):
    _, state = service.state_for(db, 'c', 'case-w')
    tool = artifact_tools(state)[0]
    assert tool.risk == ToolRisk.write
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
            await artifact_tools(state)[0].execute(ToolExecutionContext(call_id='denied'), {
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
            await service.invoke(db, 'c', 'register_case_file', {
                'case_id': 'case-w', 'arguments': {'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
                                                 'layer_type': 'segmentation', 'lut': 'freesurfer'},
                'idempotency_key': 'denied',
            })
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_shared_invocation_registers_without_run_and_checks_approval(database):
    from backend_common.db import Run

    fixture_file()
    payload = {'case_id': 'case-w', 'arguments': {
        'file_path': '/case/results with spaces/ThalamicNuclei.nii.gz',
        'layer_type': 'segmentation', 'lut': 'freesurfer'}, 'idempotency_key': 'register-once'}
    with database() as db:
        pending = await service.invoke(db, 'c', 'register_case_file', payload)
        assert pending['status'] == 'awaiting_approval', pending
        assert db.query(Artifact).count() == 0
        db.get(McpClient, 'c').require_approval = False
        db.commit()
        result = await service.invoke(db, 'c', 'register_case_file', {**payload, 'idempotency_key': 'register-authorized'})
        assert result['status'] == 'succeeded', result
        assert db.query(Artifact).count() == 1
        assert db.query(Run).count() == 0
        with pytest.raises(HTTPException):
            await service.invoke(db, 'c', 'register_case_file', {**payload, 'case_id': 'case-other', 'idempotency_key': 'wrong-case'})
