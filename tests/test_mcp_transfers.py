"""External agents import and download binary files within their paired scope."""

import hashlib
import json

import httpx
import pytest
from api_service.deps import get_db
from api_service.mcp_adapter import service
from api_service.mcp_adapter.identity import installation_id
from api_service.mcp_adapter.transfers import router
from fastapi import FastAPI
from neurocade_mcp.setup import register
from test_mcp_adapter import database as database

from backend_common.db import Artifact, McpClient


def app_for(factory):
    app = FastAPI()
    app.include_router(router)

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    return app


def volume():
    content = bytearray(400)
    content[344:348] = b'n+1\x00'
    return bytes(content)


def headers():
    return {"Authorization": "Bearer ncmcp_test", "X-NeuroCade-Installation": installation_id()}


@pytest.mark.asyncio
async def test_upload_download_resume_scope_and_replay(database):
    transport = httpx.ASGITransport(app_for(database))
    content = volume()
    digest = hashlib.sha256(content).hexdigest()
    params = {"filename": "scan.nii", "title": "agent-scan", "sha256": digest, "idempotency_key": "upload-1"}
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost", headers=headers()) as client:
        response = await client.put('/api/app/mcp/files/upload', params=params, content=content)
        assert response.status_code == 200, response.text
        case = response.json()
        again = await client.put('/api/app/mcp/files/upload', params=params, content=content)
        assert again.json() == case
        with database() as db:
            artifact = db.query(Artifact).filter_by(case_id=case['case_id']).one()
            aid = artifact.id
        download = await client.get('/api/app/mcp/files/artifacts/' + aid)
        assert download.content == content
        assert download.headers['x-checksum-sha256'] == digest
        resumed = await client.get('/api/app/mcp/files/artifacts/' + aid, headers={"Range": "bytes=100-"})
        assert resumed.status_code == 206
        assert resumed.content == content[100:]
        archive = await client.get('/api/app/mcp/files/cases/' + case['case_id'])
        assert archive.status_code == 200 and archive.content.startswith(b'PK')
        forbidden = await client.get('/api/app/mcp/files/cases/case-other')
        assert forbidden.status_code == 404
        bad_hash = await client.put('/api/app/mcp/files/upload', params={**params, 'sha256': '0' * 64}, content=content)
        assert bad_hash.status_code == 400
        conflict = await client.put('/api/app/mcp/files/upload', params={**params, 'title': 'different'}, content=content)
        assert conflict.status_code == 409
        with database() as db:
            db.get(McpClient, 'c').revoked = True
            db.commit()
        assert (await client.get('/api/app/mcp/files/artifacts/' + aid)).status_code == 401


@pytest.mark.asyncio
async def test_upload_read_only_and_installation_boundary(database):
    with database() as db:
        db.get(McpClient, 'c').access = 'read'
        db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(database)), base_url='http://localhost', headers=headers()) as client:
        response = await client.put('/api/app/mcp/files/upload', params={'filename': 'scan.nii', 'idempotency_key': 'k', 'sha256': hashlib.sha256(volume()).hexdigest()}, content=volume())
        assert response.status_code == 403
        response = await client.get('/api/app/mcp/files/cases/case-w', headers={'X-NeuroCade-Installation': 'wrong'})
        assert response.status_code == 409


def test_claude_registration_preserves_other_servers(tmp_path):
    config = tmp_path / 'claude.json'
    config.write_text(json.dumps({'mcpServers': {'other': {'command': 'keep'}}, 'preferences': {'keep': True}}))
    register('claude-desktop', 'neurocade-test', '/connector', '/private/connection.json', str(config))
    result = json.loads(config.read_text())
    assert result['mcpServers']['other'] == {'command': 'keep'}
    assert result['mcpServers']['neurocade-test']['args'] == ['connect', '--connection-file', '/private/connection.json']
    assert result['preferences']['keep']
    assert config.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_agent_confirmation_executes_without_ui_and_retries_once(database, monkeypatch):
    from api_service.assistant.tools.definition import ToolDefinition, ToolResult, ToolRisk

    calls = []

    async def execute(_context, args):
        calls.append(args)
        return ToolResult.structured({'ok': True})

    tool = ToolDefinition('mutation', 'Test', service.schema(), execute, risk=ToolRisk.write, approval_presentation=lambda _: None)
    monkeypatch.setattr(service, 'definitions', lambda _: {'mutation': tool})
    with database() as db:
        db.get(McpClient, 'c').require_approval = False
        db.commit()
        first = await service.invoke(db, 'c', 'mutation', {'arguments': {}, 'idempotency_key': 'once'})
        second = await service.invoke(db, 'c', 'mutation', {'arguments': {}, 'idempotency_key': 'once'})
        assert first['status'] == second['status'] == 'succeeded'
        assert calls == [{}]


def test_local_download_resumes_and_verifies_checksum(tmp_path, monkeypatch):
    from neurocade_mcp import transfers

    content = b'large-scan-data' * 100
    digest = hashlib.sha256(content).hexdigest()
    destination = tmp_path / 'scan.nii'
    partial = tmp_path / 'scan.nii.neurocade-part'
    metadata = tmp_path / 'scan.nii.neurocade-transfer.json'
    partial.write_bytes(content[:70])
    metadata.write_text(json.dumps({'installation_id': 'i', 'client_id': 'c', 'endpoint': 'artifacts/a', 'etag': '"v1"', 'sha256': digest}))

    def respond(request):
        assert request.headers['range'] == 'bytes=70-'
        return httpx.Response(206, headers={'X-Checksum-SHA256': digest, 'ETag': '"v1"', 'Content-Range': f'bytes 70-{len(content)-1}/{len(content)}'}, content=content[70:])

    monkeypatch.setattr(transfers, 'client_for', lambda _: httpx.Client(base_url='http://localhost/', transport=httpx.MockTransport(respond)))
    result = transfers.download({'installation_id': 'i', 'client_id': 'c'}, {'artifact_id': 'a', 'destination': str(destination)})
    assert result['sha256'] == digest
    assert destination.read_bytes() == content
    assert not partial.exists() and not metadata.exists()
    with pytest.raises(ValueError, match='does not already exist'):
        transfers.download({'installation_id': 'i', 'client_id': 'c'}, {'artifact_id': 'a', 'destination': str(destination)})


def test_local_download_rejects_corruption(tmp_path, monkeypatch):
    from neurocade_mcp import transfers

    def respond(_request):
        return httpx.Response(200, headers={'X-Checksum-SHA256': '0' * 64}, content=b'corrupted')

    monkeypatch.setattr(transfers, 'client_for', lambda _: httpx.Client(base_url='http://localhost/', transport=httpx.MockTransport(respond)))
    destination = tmp_path / 'scan.nii'
    with pytest.raises(ValueError, match='checksum mismatch'):
        transfers.download({'installation_id': 'i', 'client_id': 'c'}, {'artifact_id': 'a', 'destination': str(destination)})
    assert not destination.exists()


@pytest.mark.asyncio
async def test_busy_case_upload_keeps_retry_key_available(database):
    from backend_common.output_activity import reserve_case_files

    content = volume()
    params = {'filename': 'scan.nii', 'case_id': 'case-w',
              'sha256': hashlib.sha256(content).hexdigest(), 'idempotency_key': 'busy-retry'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(database)), base_url='http://localhost', headers=headers()) as client:
        with database() as db, reserve_case_files(db, 'w', 'case-w'):
            blocked = await client.put('/api/app/mcp/files/upload', params=params, content=content)
            assert blocked.status_code == 409
        retried = await client.put('/api/app/mcp/files/upload', params=params, content=content)
        assert retried.status_code == 200, retried.text
        assert retried.json()['case_id'] == 'case-w'


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ['case-w', None])
async def test_artifact_download_survives_original_deletion(database, monkeypatch, case_id):
    from pathlib import Path

    from api_service.mcp_adapter import transfers

    from backend_common.case_storage import case_storage_dir, workspace_storage_dir
    from backend_common.db import ArtifactKind
    from backend_common.settings import get_settings

    root = case_storage_dir(get_settings(), 'w', case_id) if case_id else workspace_storage_dir(get_settings(), 'w')
    source = root / 'report.txt'
    source.write_bytes(b'consistent snapshot')
    with database() as db:
        artifact = Artifact(workspace_id='w', case_id=case_id, name='report.txt', relative_path='report.txt',
                            kind=ArtifactKind.report, mime_type='text/plain')
        db.add(artifact)
        db.commit()
        aid = artifact.id
    response_class = transfers.FileResponse
    def delete_before_stream(path, **kwargs):
        source.unlink()
        assert Path(path) != source
        return response_class(path, **kwargs)
    monkeypatch.setattr(transfers, 'FileResponse', delete_before_stream)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(database)), base_url='http://localhost', headers=headers()) as client:
        response = await client.get('/api/app/mcp/files/artifacts/' + aid)
        assert response.status_code == 200
        assert response.content == b'consistent snapshot'
        assert response.headers['x-checksum-sha256'] == hashlib.sha256(response.content).hexdigest()


@pytest.mark.asyncio
async def test_new_case_upload_respects_workspace_writer_and_retries(database):
    from backend_common.db import Case, Run, RunStatus

    with database() as db:
        db.add(Run(id='writer', workspace_id='w', case_id=None, created_by_user_id='u', status=RunStatus.running, run_type='test'))
        db.commit()
    content = volume()
    params = {'filename': 'scan.nii', 'title': 'guarded-upload', 'idempotency_key': 'guarded',
              'sha256': hashlib.sha256(content).hexdigest()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(database)), base_url='http://localhost', headers=headers()) as client:
        assert (await client.put('/api/app/mcp/files/upload', params=params, content=content)).status_code == 409
        with database() as db:
            assert db.query(Case).filter_by(title='guarded-upload').count() == 0
            db.get(Run, 'writer').status = RunStatus.completed
            db.commit()
        assert (await client.put('/api/app/mcp/files/upload', params=params, content=content)).status_code == 200


@pytest.mark.parametrize('existing', [False, True])
def test_slow_dicom_conversion_releases_global_and_database_locks(database, monkeypatch, existing):
    import io
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from zipfile import ZipFile

    from api_service.cases import uploads
    from api_service.mcp_adapter.transfers import import_staged

    from backend_common.db import Case, Run, RunStatus
    from backend_common.output_activity import OutputBusy, ensure_outputs_idle
    from backend_common.submission_lock import submission_lock

    entered, release = threading.Event(), threading.Event()
    def conversion(_input, output):
        entered.set()
        assert release.wait(10)
        (output / 'scan.nii').write_bytes(volume())
    monkeypatch.setattr(uploads, '_run_dcm2niix', conversion)
    staged = io.BytesIO()
    with ZipFile(staged, 'w') as archive:
        archive.writestr('scan.dcm', b'test conversion input')
    content = staged.getvalue()
    def upload():
        with database() as db:
            return import_staged(db, 'c', io.BytesIO(content), 'scan.zip', None if existing else 'slow-upload',
                                 'case-w' if existing else None, 'slow-key', hashlib.sha256(content).hexdigest())
    def unrelated_submission():
        with submission_lock, database() as db:
            ensure_outputs_idle(db, 'other')
            db.add(Run(id='unrelated', workspace_id='other', case_id='case-other', created_by_user_id='u', status=RunStatus.completed, run_type='test'))
            db.commit()
            active_case = db.get(Case, 'case-w') if existing else db.query(Case).filter_by(title='slow-upload').one()
            with pytest.raises(OutputBusy):
                ensure_outputs_idle(db, 'w', active_case.id)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(upload)
        try:
            assert entered.wait(5)
            pool.submit(unrelated_submission).result(timeout=3)
        finally:
            release.set()
        assert pending.result(timeout=5)['case_id']


@pytest.mark.asyncio
async def test_failed_new_upload_removes_provisional_case(database):
    from backend_common.db import Case
    content = b'not a nifti'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(database)), base_url='http://localhost', headers=headers()) as client:
        response = await client.put('/api/app/mcp/files/upload', params={
            'filename': 'scan.nii', 'title': 'invalid-scan', 'idempotency_key': 'invalid',
            'sha256': hashlib.sha256(content).hexdigest(),
        }, content=content)
        assert response.status_code == 400
    with database() as db:
        assert db.query(Case).filter_by(title='invalid-scan').count() == 0
        assert db.query(Artifact).filter_by(name='invalid-scan.nii').count() == 0


@pytest.mark.asyncio
async def test_failed_upload_cleans_concurrent_reader_references(database, monkeypatch):
    from io import BytesIO

    from api_service.cases import operations
    from api_service.mcp_adapter.auth import resolve_client
    from fastapi import HTTPException, UploadFile

    from backend_common.case_storage import case_storage_dir
    from backend_common.db import AssistantThread, AuditEvent, Case

    observed = {}

    async def fail_upload(db, case, workspace, uploads, **kwargs):
        observed['id'] = case.id
        observed['path'] = case_storage_dir(operations.settings, workspace.id, case.id)
        with database() as reader:
            reader.add(AuditEvent(user_id='u', case_id=case.id, action='case.viewed'))
            reader.add(AssistantThread(thread_key='upload-reader', workspace_id='w',
                                       case_id=case.id, provider_name='test', model_name='test'))
            reader.commit()
        raise HTTPException(400, 'Conversion failed')

    monkeypatch.setattr(operations, '_store_uploaded_inputs', fail_upload)
    with database() as db:
        _, context = resolve_client(db, 'c')
        with pytest.raises(HTTPException, match='Conversion failed') as error:
            await operations.create_case_from_upload(
                db, context, workspace_id='w', title='provisional', description=None,
                modalities=None, tags=None, notes=None,
                file=UploadFile(BytesIO(b'x'), filename='scan.nii'), files=None,
            )
        assert error.value.status_code == 400
        assert db.get(Case, observed['id']) is None
        assert db.query(AuditEvent).filter_by(case_id=observed['id']).count() == 0
        assert db.query(AssistantThread).filter_by(thread_key='upload-reader').count() == 0
        assert not observed['path'].exists()


def test_failed_upload_cleanup_restores_storage_if_database_cleanup_fails(database, monkeypatch):
    from api_service.cases import operations

    from backend_common.case_storage import case_storage_dir
    from backend_common.db import Case

    def fail_cleanup(db, case):
        db.delete(case)
        raise RuntimeError('Database cleanup failed')

    monkeypatch.setattr(operations, '_purge_case_rows', fail_cleanup)
    with database() as db:
        case = db.get(Case, 'case-w')
        path = case_storage_dir(operations.settings, case.workspace_id, case.id)
        marker = path / 'preserve.txt'
        marker.write_text('retain on rollback')
        with operations.reserve_case_file_update(db, case), pytest.raises(RuntimeError, match='Database cleanup failed'):
            operations._discard_failed_upload(db, case.id, storage_created=True)
        assert db.get(Case, 'case-w') is not None
        assert marker.read_text() == 'retain on rollback'
