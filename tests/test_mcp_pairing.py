"""One-time pairing permissions, races, expiry, and host credential reuse."""

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest
from api_service.deps import get_context, get_db
from api_service.mcp_adapter.management import router
from api_service.mcp_adapter.pairing import PairingCreate, PairingRedeem, create_pairing, redeem_pairing
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from neurocade_mcp.pairing import paired_connection
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend_common.case_storage import ensure_workspace_storage_layout
from backend_common.db import (
    Base,
    McpClient,
    McpPairing,
    RoleEnum,
    User,
    Workspace,
    WorkspaceMembership,
    _configure_sqlite_connection,
    _sqlite_begin,
)
from backend_common.settings import get_settings


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), 'fs_data_root', tmp_path / 'data')
    monkeypatch.setattr(get_settings(), 'mcp_enabled', True)
    monkeypatch.setattr(get_settings(), 'mcp_access', 'standard')
    monkeypatch.setattr('api_service.mcp_adapter.pairing.installation_id', lambda: 'installation-test')
    monkeypatch.setenv('NEUROCADE_MCP_HOST_EXECUTABLE', '/host/neurocade-mcp')
    engine = create_engine(f'sqlite:///{tmp_path / "pairing.db"}', connect_args={'check_same_thread': False})
    event.listen(engine, 'connect', _configure_sqlite_connection)
    event.listen(engine, 'begin', _sqlite_begin)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(id=uid, email=f'{uid}@example.com', full_name=uid) for uid in ['one', 'two']])
        db.flush()
        workspace = Workspace(id='w', owner_user_id='one', name='personal-workspace')
        db.add(workspace)
        db.flush()
        ensure_workspace_storage_layout(get_settings(), workspace)
        db.add(WorkspaceMembership(user_id='one', workspace_id='w', role=RoleEnum.owner))
        db.commit()
    app = FastAPI()
    app.include_router(router)

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_context] = lambda: SimpleNamespace(user=SimpleNamespace(id='one'))
    with TestClient(app, base_url='http://localhost:8000') as client:
        yield factory, app, client
    engine.dispose()


def grant(client):
    response = client.post('/api/app/mcp/pairings', json={'name': 'Test', 'workspace_id': 'w'}, headers={'X-NeuroCade-UI': '1'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def body(data):
    return {key: data[key] for key in ['pairing_id', 'code', 'installation_id']}


def test_pairing_scope_and_auth(environment):
    factory, app, client = environment
    endpoint = '/api/app/mcp/pairings'
    headers = {'X-NeuroCade-UI': '1'}
    create = {'name': 'Test', 'workspace_id': 'w'}
    assert client.post(endpoint, json=create).status_code == 403
    assert client.post(endpoint, json=create, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post(endpoint, json=create, headers={**headers, 'Authorization': 'Bearer ncmcp_token'}).status_code == 403
    app.dependency_overrides[get_context] = lambda: SimpleNamespace(user=SimpleNamespace(id='two'))
    assert client.post(endpoint, json=create, headers=headers).status_code == 404
    app.dependency_overrides[get_context] = lambda: SimpleNamespace(user=SimpleNamespace(id='one'))
    data = grant(client)
    with factory() as db:
        row = db.get(McpPairing, data['pairing_id'])
        assert row.code_hash != data['code']
        assert db.query(McpClient).count() == 0
    # Redemption needs only the grant, never a logged-in browser or agent token.
    def no_login():
        raise AssertionError('redemption must not require UI login')
    app.dependency_overrides[get_context] = no_login
    response = client.post(endpoint + '/redeem', json=body(data), headers=headers)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    token = response.json()['token']
    with factory() as db:
        connection = db.query(McpClient).one()
        assert connection.user_id == 'one' and connection.workspace_id == 'w'
        assert not connection.require_approval and connection.token_hash != token
    assert client.post(endpoint + '/redeem', json=body(data), headers=headers).status_code == 410


def test_expiry_identity_and_membership(environment):
    factory, _, client = environment
    data = grant(client)
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            redeem_pairing(db, PairingRedeem(**{**body(data), 'installation_id': 'other'}), 'http://localhost:8000')
        assert exc.value.status_code == 409
        db.get(McpPairing, data['pairing_id']).expires_at = int(time.time()) - 1
        db.commit()
        with pytest.raises(HTTPException) as exc:
            redeem_pairing(db, PairingRedeem(**body(data)), 'http://localhost:8000')
        assert exc.value.status_code == 410
    data = grant(client)
    with factory() as db:
        db.query(WorkspaceMembership).delete()
        db.commit()
        with pytest.raises(HTTPException):
            redeem_pairing(db, PairingRedeem(**body(data)), 'http://localhost:8000')
        assert db.query(McpClient).count() == 0


def test_concurrent_redemption(environment):
    factory, _, client = environment
    data = grant(client)
    def redeem():
        with factory() as db:
            try:
                return redeem_pairing(db, PairingRedeem(**body(data)), 'http://localhost:8000')
            except HTTPException as exc:
                return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: redeem(), range(2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert 410 in results


def test_extension_contains_single_use_grant(environment):
    _, _, client = environment
    response = client.post('/api/app/mcp/desktop-extension', headers={'X-NeuroCade-UI': '1'}, json={'name': 'Claude Desktop', 'workspace_id': 'w'})
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        config = json.loads(archive.read('installation.json'))
        assert 'user_config' not in manifest
        assert manifest['server']['mcp_config']['args'] == ['${__dirname}/launcher.cjs']
        assert 'token' not in config and config['code'].startswith('ncpair_')
        assert config['expires_at'] <= time.time() + 600


def test_host_cache_once_and_restart(environment, tmp_path, monkeypatch):
    _, _, api = environment
    data = grant(api)
    original = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        response = api.post('/api/app/mcp/pairings/redeem', json=json.loads(request.content), headers={'X-NeuroCade-UI': '1'})
        return httpx.Response(response.status_code, json=response.json())
    monkeypatch.setattr('neurocade_mcp.pairing.httpx.Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    directory = tmp_path / 'private'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: paired_connection(data, directory), range(2)))
    assert results[0] == results[1]
    assert len(calls) == 1
    assert results[0].stat().st_mode & 0o777 == 0o600
    assert data['code'] not in results[0].read_text()
    # No server round trip needed after expiry when credentials are already cached.
    assert paired_connection({**data, 'expires_at': 0}, directory) == results[0]
    assert len(calls) == 1
    with pytest.raises(ValueError, match='expired or was already used'):
        paired_connection(data, tmp_path / 'different-device')
    with pytest.raises(ValueError, match='local NeuroCade'):
        paired_connection({**data, 'url': 'https://evil.invalid/mcp'}, directory)


def test_read_profile_and_policy(environment, monkeypatch):
    factory, _, _ = environment
    monkeypatch.setattr(get_settings(), 'mcp_access', 'read')
    with factory() as db:
        with pytest.raises(HTTPException):
            create_pairing(db, 'one', PairingCreate(name='test', workspace_id='w'), 'http://localhost:8000')
        data = create_pairing(db, 'one', PairingCreate(name='test', workspace_id='w', access='read', require_approval=True), 'http://localhost:8000')
        saved = redeem_pairing(db, PairingRedeem(**body(data)), 'http://localhost:8000')
        connection = db.get(McpClient, saved['client_id'])
        assert connection.access == 'read' and connection.require_approval


@pytest.mark.parametrize('target', ['chatgpt', 'codex-plugin', 'claude-code', 'manual'])
def test_setup_adapters_use_same_pairing(environment, tmp_path, monkeypatch, capsys, target):
    from unittest.mock import AsyncMock

    from neurocade_mcp.setup import setup

    _, _, api = environment
    data = grant(api)
    original = httpx.Client
    calls = []
    def respond(request):
        response = api.post('/api/app/mcp/pairings/redeem', json=json.loads(request.content), headers={'X-NeuroCade-UI': '1'})
        return httpx.Response(response.status_code, json=response.json())
    monkeypatch.setattr('neurocade_mcp.pairing.httpx.Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr('neurocade_mcp.connect', AsyncMock())
    monkeypatch.setattr('neurocade_mcp.setup.register', lambda *args: calls.append(args))
    args = SimpleNamespace(url='http://localhost:8000', client=target, pairing_code=data['code'], pairing_id=data['pairing_id'],
        installation_id=data['installation_id'], workspace=None, create_workspace=None, list_workspaces=False,
        require_approval=False, connection_dir=tmp_path / 'credentials', config_path=None)
    setup(args)
    setup(args)
    assert calls[0] == calls[1] and calls[0][0] == target
    output = capsys.readouterr().out
    assert data['code'] not in output and 'ncmcp_' not in output
    args.workspace = 'different'
    with pytest.raises(ValueError, match='already fixes the workspace'):
        setup(args)


def test_new_grant_replaces_same_local_registration_only(tmp_path, monkeypatch):
    from neurocade_mcp.setup import register_connection

    calls = []
    monkeypatch.setattr('neurocade_mcp.setup.register', lambda *args: calls.append(args))
    first = {'installation_id': 'installation', 'user_id': 'user', 'workspace_id': 'workspace',
             'client_id': 'old', 'token': 'ncmcp_old', 'url': 'http://localhost:8000/mcp'}
    name, path, _ = register_connection('chatgpt', '/connector', tmp_path / 'grant-one.json', first)
    second = {**first, 'client_id': 'new', 'token': 'ncmcp_new'}
    next_name, next_path, _ = register_connection('chatgpt', '/connector', tmp_path / 'grant-two.json', second)
    assert (name, path) == (next_name, next_path)
    assert calls[0] == calls[1]
    assert json.loads(path.read_text())['client_id'] == 'new'
    assert path.stat().st_mode & 0o777 == 0o600
    assert register_connection('claude-code', '/connector', tmp_path / 'third.json', second)[0] != name
    assert register_connection('chatgpt', '/connector', tmp_path / 'fourth.json', {**second, 'user_id': 'other'})[0] != name
    assert register_connection('chatgpt', '/connector', tmp_path / 'fifth.json', {**second, 'workspace_id': 'other'})[0] != name


def test_manual_connections_keep_separate_credentials(tmp_path):
    from neurocade_mcp.setup import private_json, register_connection

    first = {'installation_id': 'installation', 'user_id': 'user', 'workspace_id': 'workspace',
             'client_id': 'first', 'token': 'ncmcp_first', 'url': 'http://localhost:8000/mcp'}
    second = {**first, 'client_id': 'second', 'token': 'ncmcp_second'}
    first_path, second_path = tmp_path / 'first.json', tmp_path / 'second.json'
    private_json(first_path, first)
    private_json(second_path, second)
    one = register_connection('manual', '/connector', first_path, first)
    two = register_connection('manual', '/connector', second_path, second)
    assert one[0] != two[0]
    assert one[1] == first_path
    assert two[1] == second_path
    assert json.loads(first_path.read_text()) == first
    assert json.loads(second_path.read_text()) == second
