"""Opt-in real-container authoring checks; never uses the running app or bridge."""
import asyncio
import os
import secrets
import subprocess
import threading
from pathlib import Path

import pytest
import yaml
from api_service.assistant.tools.definition import ToolExecutionContext
from api_service.assistant.tools.probe_tools import AssistantProbeTools
from api_service.assistant.tools.workflow_test_tools import AssistantWorkflowTestTools
from neurocade_runtime_tools.bridge import BridgeRuntime
from neurocade_runtime_tools.bridge_server import BridgeHTTPServer

from backend_common.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get('RUN_AUTHORING_SANDBOX') != '1', reason='Opt in to isolated Docker checks')


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    for image in ('vnmd/fsl_6.0.7.23:20261013', 'vnmd/freesurfer_8.2.0:20261005'):
        result = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True)
        if result.returncode:
            pytest.skip('Cache the benchmark images before running; this test does not install them')
    runtime = BridgeRuntime(backend='docker', data_root=tmp_path, image_dir=tmp_path / 'images', launch_id='authoring-test')
    token = secrets.token_hex(32)
    token_path = tmp_path / 'token'
    token_path.write_text(token)
    token_path.chmod(0o600)
    server = BridgeHTTPServer(('127.0.0.1', 0), runtime, token, launch_id='authoring-test')
    monkeypatch.setenv('HOST_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('NEUROCADE_RUNTIME', 'docker')
    monkeypatch.setenv('NEUROCADE_LAUNCH_ID', 'authoring-test')
    monkeypatch.setenv('NEUROCADE_BRIDGE_TOKEN_FILE', str(token_path))
    monkeypatch.setenv('NEUROCADE_BRIDGE_URL', f'http://127.0.0.1:{server.server_port}')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Settings(_env_file=None, HOST_DATA_DIR=tmp_path), tmp_path
    finally:
        runtime.shutdown()
        server.shutdown()
        server.server_close()


def test_inspect_and_staging_cannot_access_host_case(sandbox):
    settings, root = sandbox
    context = ToolExecutionContext('sandbox')
    inspected = asyncio.run(AssistantProbeTools(settings=settings).inspect({}, context, {
        'image': 'vnmd/fsl_6.0.7.23:20261013', 'executable': 'fslmaths', 'action': 'help', 'arguments': ['-h'],
    }))
    assert not inspected.is_error and '-Tmean' in inspected.details['stdout'], inspected.content
    invalid_bytes = asyncio.run(AssistantProbeTools(settings=settings).inspect({}, context, {
        'image': 'vnmd/fsl_6.0.7.23:20261013', 'executable': 'printf', 'arguments': [r'\377'],
    }))
    assert not invalid_bytes.is_error and '\ufffd' in invalid_bytes.details['stdout'], invalid_bytes.content
    definition = yaml.safe_load((Path(__file__).parents[1] / 'fixtures/workflows/freesurfer_segmentbs.yaml').read_text())
    definition['case_output_folder'] = 'thalamus'
    current_case = root / 'current-case'
    current_case.mkdir()
    sentinel = current_case / 'sentinel'
    sentinel.write_text('protected')
    tools = AssistantWorkflowTestTools(settings=settings)
    for expected, script in (
        ('staging_passed', definition['script']),
        ('staging_failed', 'SOURCE="${INPUTS[0]}"\ncp -a "$SOURCE/." "${OUTPUT_ROOT}"'),
        ('staging_failed', f'rm -f "{sentinel}"'),
    ):
        result = asyncio.run(tools.test({}, context, {
            'mode': 'staging', 'definition': {**definition, 'script': script}, 'processing_command': 'segmentBS.sh',
        }))
        assert result.details and result.details['status'] == expected, result.content
        assert sentinel.read_text() == 'protected'
    assert not list(root.glob('.workflow-test-*'))
