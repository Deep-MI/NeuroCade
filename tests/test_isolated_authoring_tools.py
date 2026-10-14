"""Authoring boundaries: the model cannot supply case mounts or shell inspection."""
import asyncio
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from api_service.assistant.tools import probe_tools, workflow_test_tools
from api_service.assistant.tools.definition import ToolExecutionContext
from neurocade_runtime_tools.apptainer_runtime import build_container_argv
from neurocade_runtime_tools.docker_runtime import build_docker_argv
from neurocade_runtime_tools.execution import BridgeBind, RuntimeContainerRunRequest, validate_synthetic_fixture
from neurocade_runtime_tools.protocol import RuntimeImageSpec
from pydantic import ValidationError


def draft():
    value = yaml.safe_load((Path(__file__).parent / 'fixtures/workflows/freesurfer_segmentbs.yaml').read_text())
    value['case_output_folder'] = 'thalamus'
    return value


def test_only_two_tools_and_no_model_mount_fields():
    tools = probe_tools.AssistantProbeTools(settings=None).build_tools({})
    assert {tool.name for tool in tools} == {'tool_inspect', 'tool_workflow_test'}
    with pytest.raises(ValidationError):
        probe_tools.ToolInspectArgs.model_validate({'image': 'vnmd/test:1', 'executable': 'ls', 'script': 'rm -rf /case'})
    with pytest.raises(ValidationError):
        workflow_test_tools.WorkflowTestArgs.model_validate({'definition': draft(), 'processing_command': 'segmentBS.sh', 'case_id': 'live'})


def test_inspection_quotes_literal_arguments(tmp_path):
    marker = tmp_path / 'injection'
    argument = '$(touch ' + str(marker) + ')'
    args = probe_tools.ToolInspectArgs(image='vnmd/test:1', executable='printf', arguments=['%s', argument])
    result = subprocess.run(['bash', '-c', probe_tools.inspection_script(args)], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == argument
    assert not marker.exists()


def test_inspection_has_no_case_mounts_or_credentials(monkeypatch):
    async def execute(request, **kwargs):
        run = request.container_run
        assert run.isolated and not run.synthetic_fixture
        assert not run.binds and not run.gpu_enabled and run.network_disabled
        assert set(run.env) == {'TMPDIR', 'LC_ALL', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS'}
        return SimpleNamespace(returncode=0, stdout='Usage: test', stderr='')
    monkeypatch.setattr(probe_tools, 'execute_runtime_request_async', execute)
    result = asyncio.run(probe_tools.AssistantProbeTools(settings=None).inspect(
        {'case_id': 'live', 'secret': 'never forward'}, ToolExecutionContext('test'),
        {'image': 'vnmd/test:1', 'executable': 'test', 'action': 'help'}))
    assert result.details['status'] == 'completed'
    assert result.details['assertions'] == 'not_run'


def synthetic_request(tmp_path):
    case = tmp_path / '.workflow-test-random/case with spaces'
    output = case / 'thalamus'
    output.mkdir(parents=True)
    return RuntimeContainerRunRequest(
        image=RuntimeImageSpec('vnmd/test:1'), command=['bash', '-c', 'true'],
        binds=(BridgeBind(str(case.relative_to(tmp_path)), '/case with spaces', 'rw'),
               BridgeBind(str(output.relative_to(tmp_path)), '/workflow_output/output', 'rw')),
        isolated=True, synthetic_fixture=True, scratch_paths=('/workflow_output',),
    )


@pytest.mark.parametrize('mutation', ['case', 'symlink', 'network', 'isolation', 'extra_mount', 'linked', 'scratch'])
def test_synthetic_boundary_rejects_escape_on_both_backends(tmp_path, mutation):
    request = synthetic_request(tmp_path)
    live = tmp_path / 'output/workspaces/live/cases/current'
    live.mkdir(parents=True)
    if mutation == 'case':
        request.binds = (replace(request.binds[0], source_relative=str(live.relative_to(tmp_path))), request.binds[1])
    elif mutation == 'symlink':
        case = tmp_path / request.binds[0].source_relative
        (case / 'thalamus').rmdir()
        (case / 'thalamus').symlink_to(live, target_is_directory=True)
    elif mutation == 'network':
        request.network_disabled = False
    elif mutation == 'isolation':
        request.isolated = False
    elif mutation == 'extra_mount':
        request.binds = (*request.binds, BridgeBind('.', '/data', 'rw'))
    elif mutation == 'linked':
        request.binds = (replace(request.binds[0], host_path=live), request.binds[1])
    else:
        request.scratch_paths = ('/data',)
    image = tmp_path / 'test.sif'
    image.touch()
    with pytest.raises(ValueError):
        validate_synthetic_fixture(request, tmp_path)
    with pytest.raises(ValueError):
        build_docker_argv(request, data_root=tmp_path, platform='linux/amd64')
    with pytest.raises(ValueError):
        build_container_argv(request, data_root=tmp_path, prepared_image=image)


def test_synthetic_backends_keep_sandbox(tmp_path):
    request = synthetic_request(tmp_path)
    docker = build_docker_argv(request, data_root=tmp_path, platform='linux/amd64')
    assert '--network' in docker and 'none' in docker and '--read-only' in docker
    assert '--cap-drop' in docker and '--memory' in docker
    assert '/tmp:rw,nosuid,nodev,noexec' in docker
    image = tmp_path / 'test.sif'
    image.touch()
    apptainer = build_container_argv(request, data_root=tmp_path, prepared_image=image)
    assert '--no-home' in apptainer and '--containall' in apptainer
    assert 'hostfs,cwd' in apptainer and 'none' in apptainer


@pytest.mark.parametrize('broken', [False, True])
def test_workflow_grading_uses_own_fixture_and_two_runs(tmp_path, monkeypatch, broken):
    calls = []
    async def execute(request, **kwargs):
        run = request.container_run
        calls.append(run)
        assert run.isolated and run.synthetic_fixture and run.network_disabled
        case = Path(run.binds[0].host_path)
        output = Path(run.binds[1].host_path)
        assert case.parent.parent == tmp_path
        assert case.parent.name.startswith('.workflow-test-')
        assert output.parent == case and len(run.binds) == 2
        # Unit test the exact shell without Docker. Real aliases are covered by
        # the opt-in integration run; host paths here intentionally share a directory.
        script = run.command[-1].replace('/case with spaces', str(case)).replace('/workflow_output/output', str(output)).replace('/workflow_output', str(case))
        script = script.replace('readonly OUTPUT_NAME=output', 'readonly OUTPUT_NAME=thalamus')
        lines = []
        for line in script.splitlines():
            if line.startswith('ulimit '):
                continue
            if line.startswith('readonly OUTPUT_ROOT='):
                line = 'readonly OUTPUT_ROOT=' + shlex.quote(str(output))
            if line.startswith('readonly OUTPUT_PARENT='):
                line = 'readonly OUTPUT_PARENT=' + shlex.quote(str(case))
            if line.startswith('readonly -a OUTPUTS='):
                line = 'readonly -a OUTPUTS=()'
            if line.startswith('mkdir -p -- '):
                continue
            lines.append(line)
        script = '\n'.join(lines)
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
        return SimpleNamespace(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    monkeypatch.setattr(workflow_test_tools, 'execute_runtime_request_async', execute)
    value = draft()
    if broken:
        value['script'] = 'true'
    result = asyncio.run(workflow_test_tools.AssistantWorkflowTestTools(settings=SimpleNamespace(fs_data_root=tmp_path)).test(
        {'case_id': 'live'}, ToolExecutionContext('test'), {'definition': value, 'mode': 'staging', 'processing_command': 'segmentBS.sh'}))
    assert result.is_error == broken, result.content
    assert len(calls) == (1 if broken else 2)
    assert result.details['case_mounted'] is False
    assert not list(tmp_path.glob('.workflow-test-*'))


def test_validate_mode_never_starts_container(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Validation must not start a container')
    monkeypatch.setattr(workflow_test_tools, 'execute_runtime_request_async', forbidden)
    result = asyncio.run(workflow_test_tools.AssistantWorkflowTestTools(settings=None).test(
        {}, ToolExecutionContext('review'), {'definition': draft(), 'mode': 'validate'}))
    assert not result.is_error
    assert result.details['status'] == 'schema_valid'
    assert result.details['saved'] is False and result.details['executed'] is False


def test_replaced_fixture_root_cannot_redirect_host_grading(tmp_path, monkeypatch):
    outside = tmp_path / 'current-case'
    outside.mkdir()
    sentinel = outside / 'keep.txt'
    sentinel.write_text('protected')
    async def execute(request, **kwargs):
        output = Path(request.container_run.binds[1].host_path)
        (output / 'keep.txt').unlink()
        output.rmdir()
        output.symlink_to(outside, target_is_directory=True)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(workflow_test_tools, 'execute_runtime_request_async', execute)
    result = asyncio.run(workflow_test_tools.AssistantWorkflowTestTools(settings=SimpleNamespace(fs_data_root=tmp_path)).test(
        {}, ToolExecutionContext('test'), {'definition': draft(), 'mode': 'staging', 'processing_command': 'segmentBS.sh'}))
    assert result.is_error
    assert result.details['attempts'][0]['failures'] == ['fixture_root_replaced']
    assert sentinel.read_text() == 'protected'


def test_configured_workflow_inspection_remains_available(monkeypatch):
    from api_service.assistant.tools.catalog_tools import AssistantCatalogTools
    from api_service.assistant.tools.definition import ToolResult
    monkeypatch.setattr(AssistantCatalogTools, 'inspect', lambda self, state, execution, args: ToolResult.structured({'id': args['tool_id']}))
    result = asyncio.run(probe_tools.AssistantProbeTools(settings=None).inspect(
        {}, ToolExecutionContext('inspect'), {'tool_id': 'configured-workflow'}))
    assert result.details['id'] == 'configured-workflow'


@pytest.mark.parametrize('field,value', [('stdout_relative', 'current-case/result'), ('stderr_relative', 'current-case/log'), ('workdir_relative', 'current-case'), ('timeout_s', 31)])
def test_bridge_rejects_host_logs_and_unbounded_synthetic_tests(tmp_path, field, value):
    from neurocade_runtime_tools.bridge import BridgeRuntime
    from neurocade_runtime_tools.protocol import PROTOCOL_VERSION
    request = synthetic_request(tmp_path)
    payload = {
        'protocol_version': PROTOCOL_VERSION, 'run_id': 'test', 'timeout_s': 30,
        'container': {'image': request.image.to_dict(), 'command': ['true'], 'isolated': True,
                      'synthetic_fixture': True, 'network_disabled': True,
                      'scratch_paths': ['/workflow_output'],
                      'binds': [{'source_relative': bind.source_relative, 'container_path': bind.container_path, 'mode': bind.mode}
                                for bind in request.binds]},
    }
    runtime = BridgeRuntime(backend='docker', data_root=tmp_path, image_dir=tmp_path / 'images')
    runtime._validated_request(payload)
    payload[field] = value
    with pytest.raises(ValueError):
        runtime._validated_request(payload)


def test_source_inspection_rejects_binary_before_emitting_bytes():
    args = probe_tools.ToolInspectArgs(image='vnmd/test:1', executable='/bin/bash', action='source')
    result = subprocess.run(['bash', '-c', probe_tools.inspection_script(args)], capture_output=True, text=True)
    assert result.returncode == 65
    assert not result.stdout
    assert 'compiled binary' in result.stderr
