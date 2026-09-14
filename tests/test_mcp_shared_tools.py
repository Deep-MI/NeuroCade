"""MCP and embedded models share tool registries, prompts and permission checks."""

import pytest
from api_service.assistant.prompts import build_system_prompt
from api_service.gui_state import build_gui_state_session_key
from api_service.mcp_adapter import service
from api_service.mcp_adapter.prompts import instructions
from api_service.runtime.gui_runtime import gui_runtime
from fastapi import HTTPException
from test_mcp_adapter import database as database

from backend_common.db import McpClient
from backend_common.settings import ROOT_DIR


@pytest.mark.parametrize('case_id', [None, 'case-w'])
def test_registry_and_prompt_parity(database, case_id):
    with database() as db:
        _, state = service.state_for(db, 'c', case_id)
        builder = service.tool_builder()
        local = builder.build(state)[0]
        external = service.definitions(state)
        assert {tool.name for tool in local} == set(external)
        for tool in local:
            assert tool.parameters == external[tool.name].parameters
            assert tool.description == external[tool.name].description
            assert tool.risk == external[tool.name].risk
        advertised = {tool.name for tool in builder.discover(state)}
        assert set(external) <= advertised
        assert {'read', 'write', 'edit', 'tool_config_upsert', 'tool_config_delete', 'tool_probe', 'tool_image_search', 'gui_load_layer', 'workspace_file_tree', 'get_case'} <= advertised
        state['tool_specs'] = builder.build(state)[1]
        assert build_system_prompt(ROOT_DIR / 'config', state) in instructions(state)
        for name in ['SOUL.md', 'INFORMATION.md', 'RULES.md']:
            assert (ROOT_DIR / 'config' / name).read_text().strip() in instructions(state)


@pytest.mark.asyncio
async def test_shared_file_tools_execute_and_enforce_scope(database):
    with database() as db:
        db.get(McpClient, 'c').require_approval = False
        db.commit()
        payload = {'case_id': 'case-w', 'arguments': {'path': '/case/agent.txt', 'content': 'shared tools'}, 'idempotency_key': 'write-once'}
        result = await service.invoke(db, 'c', 'write', payload)
        assert result['status'] == 'succeeded', result
        result = await service.invoke(db, 'c', 'read', {'case_id': 'case-w', 'arguments': {'path': '/case/agent.txt'}})
        assert 'shared tools' in result['result']['content']
        with pytest.raises(HTTPException):
            await service.invoke(db, 'c', 'read', {'case_id': 'case-other', 'arguments': {'path': '/case/agent.txt'}})
        db.get(McpClient, 'c').access = 'read'
        db.commit()
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, 'c', 'write', {**payload, 'idempotency_key': 'blocked'})
        assert exc.value.status_code == 403


def test_viewer_session_scope(database):
    key = build_gui_state_session_key(user_id='u', workspace_id='w', case_id='case-w', gui_session_id='test-viewer')
    gui_runtime.sync_gui_state({'workspace_id': 'w', 'case_id': 'case-w'}, gui_state_key=key)
    with database() as db:
        _, state = service.state_for(db, 'c', 'case-w', 'test-viewer')
        assert state['gui_session_id'] == 'test-viewer'
        with pytest.raises(HTTPException):
            service.state_for(db, 'c', 'case-w', 'other-viewer')
        assert not gui_runtime.gui_state_store.sessions(user_id='another-user', workspace_id='w')


@pytest.mark.asyncio
async def test_gui_changes_require_selected_session(database):
    with database() as db:
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, 'c', 'gui_move_cursor', {'case_id': 'case-w', 'arguments': {'x': 1, 'y': 2, 'z': 3}})
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_gui_commands_reach_only_selected_viewer(database):
    key = build_gui_state_session_key(user_id='u', workspace_id='w', case_id='case-w', gui_session_id='selected-viewer')
    gui_runtime.sync_gui_state({'workspace_id': 'w', 'case_id': 'case-w', 'layers': [{'id': 'scan', 'filename': 'scan.nii', 'type': 'volume'}]}, gui_state_key=key)
    with database() as db:
        response = await service.invoke(db, 'c', 'gui_move_cursor', {'case_id': 'case-w', 'gui_session_id': 'selected-viewer', 'arguments': {'x': 1, 'y': 2, 'z': 3}})
        assert response['status'] == 'succeeded'
        commands = gui_runtime.gui_state_store.state_for_key(key)['commands']
        assert commands[-1]['payload']['position'] == [1, 2, 3]
        db.get(McpClient, 'c').access = 'read'
        db.commit()
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, 'c', 'gui_move_cursor', {'case_id': 'case-w', 'gui_session_id': 'selected-viewer', 'arguments': {'x': 4, 'y': 5, 'z': 6}})
        assert exc.value.status_code == 403
        assert len(gui_runtime.gui_state_store.state_for_key(key)['commands']) == len(commands)


@pytest.mark.asyncio
@pytest.mark.parametrize('modality,sequence,shape,message', [
    ('CT', 'T1', (3, 3, 3), 'modality'),
    ('MR', 'other', (3, 3, 3), 'sequence'),
    ('MR', 'T1', (3, 3, 3, 2), 'dimensions'),
])
async def test_prepare_analysis_rejects_incompatible_pacs_before_approval(database, modality, sequence, shape, message):
    import numpy as np
    from api_service.pacs.outputs import fingerprint
    from nibabel.nifti1 import Nifti1Image

    from backend_common.case_storage import case_storage_dir
    from backend_common.db import Artifact, ArtifactKind, Run
    from backend_common.settings import get_settings

    path = case_storage_dir(get_settings(), 'w', 'case-w') / 'scan.nii'
    Nifti1Image(np.zeros(shape), np.eye(4)).to_filename(path)
    with database() as db:
        artifact = Artifact(workspace_id='w', case_id='case-w', kind=ArtifactKind.volume,
                            name='scan.nii', relative_path='scan.nii', metadata_json={
                                'source': 'pacs', 'modality': modality, 'sequence': sequence,
                                'sha256': fingerprint(path),
                            })
        db.add(artifact)
        db.commit()
        result = await service.invoke(db, 'c', 'prepare_analysis', {
            'case_id': 'case-w', 'arguments': {'tool_id': 'fastsurfer_fast', 'artifact_ids': [artifact.id]},
        })
        assert result['result']['is_error'], result
        assert message in result['result']['content']
        assert db.query(Run).count() == 0


@pytest.mark.asyncio
async def test_canonical_case_inspection_is_paged_scoped_and_keeps_status(database):
    from datetime import datetime

    from backend_common.db import Case, Run, RunStatus

    with database() as db:
        db.add(Case(id='z-case', workspace_id='w', owner_user_id='u', title='second'))
        when = datetime(2026, 1, 1)
        db.add_all([Run(id=identity, workspace_id='w', case_id='case-w', created_by_user_id='u',
                        run_type='test', status=status, created_at=when)
                    for identity, status in [('a-old', RunStatus.failed), ('z-new', RunStatus.completed)]])
        db.commit()
        _, state = service.state_for(db, 'c')
        names = [tool.name for tool in service.tool_builder().discover(state)]
        assert names.count('list_cases') == names.count('get_case') == 1
        assert not {'workspace_list_cases', 'case_info'} & set(names)
        first = await service.invoke(db, 'c', 'list_cases', {'arguments': {'limit': 1}})
        page = first['result']['details']
        assert page['cases'][0]['case_id'] == 'case-w'
        assert page['cases'][0]['latest_run_status'] == 'completed'
        assert page['cases'][0]['workspace_path']
        second = await service.invoke(db, 'c', 'list_cases', {'arguments': {'limit': 1, 'after': page['next_cursor']}})
        assert [case['case_id'] for case in second['result']['details']['cases']] == ['z-case']
        assert second['result']['details']['next_cursor'] is None
        case = await service.invoke(db, 'c', 'get_case', {'case_id': 'case-w', 'arguments': {}})
        assert 'case-w' in case['result']['content']


@pytest.mark.asyncio
async def test_get_case_selects_workspace_case_without_escaping_scope(database):
    from api_service.assistant.tools.definition import ToolExecutionContext

    with database() as db:
        _, state = service.state_for(db, 'c')
        tool = next(tool for tool in service.tool_builder().build(state)[0] if tool.name == 'get_case')
        result = await tool.execute(ToolExecutionContext(call_id='inspect'), {'case_id': 'case-w'})
        assert 'case-w' in result.content
        with pytest.raises(HTTPException) as error:
            await tool.execute(ToolExecutionContext(call_id='outside'), {'case_id': 'case-other'})
        assert error.value.status_code == 404
        result = await service.invoke(db, 'c', 'get_case', {'arguments': {'case_id': 'case-w'}})
        assert 'case-w' in result['result']['content']
        _, state = service.state_for(db, 'c', 'case-w')
        tool = next(tool for tool in service.tool_builder().build(state)[0] if tool.name == 'get_case')
        with pytest.raises(HTTPException) as error:
            await tool.execute(ToolExecutionContext(call_id='escape'), {'case_id': 'case-other'})
        assert error.value.status_code == 404
