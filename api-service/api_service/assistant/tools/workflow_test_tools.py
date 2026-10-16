"""Run real workflow commands against independent, disposable input copies."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from neurocade_runtime_tools.bridge_client import RuntimeStoppedTimeout
from neurocade_runtime_tools.container_request import build_container_request
from neurocade_runtime_tools.execution import RuntimeBind, RuntimeExecutionRequest, execute_runtime_request_async
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from api_service.assistant.tools.catalog_tools import AssistantCatalogTools, _inline_schema
from api_service.assistant.tools.definition import ToolExecutionContext, ToolResult
from api_service.assistant.tools.file_tools import AssistantFileTools
from api_service.assistant.tools.registration import ToolRegistration
from api_service.runtime_tools.runtime_images import runtime_image_spec
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow
from api_service.runtime_tools.workflow_execution import prepare_workflow, workflow_script

# Retain task references until completion; status and logs themselves live on disk.
_TASKS: dict[str, asyncio.Task] = {}
COPY_LIMIT = 50 * 1024**3
LOG_LIMIT = 10 * 1024**2


class WorkflowTestArgs(BaseModel):
    model_config = {"extra": "forbid"}
    mode: Literal['staging', 'validate', 'status', 'logs'] = 'staging'
    definition: NeuroimagingWorkflow | None = None
    inputs: list[str] = Field(default_factory=list, description='Ordered input selections relative to the active case, or /case/...; copied independently before execution.')
    timeout_s: int = Field(default=10, ge=1, le=86400, description='Execution timeout, excluding input copying and image preparation. For more than 60 seconds ask the user first unless already explicitly authorized. No extra approval is needed when already authorized.')
    test_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    stream: Literal['stdout', 'stderr'] = 'stdout'
    offset_bytes: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=20000, ge=1, le=50000)

    @model_validator(mode='after')
    def validate_operation(self):
        if self.mode in {'status', 'logs'}:
            if not self.test_id or self.definition is not None or self.inputs:
                raise ValueError('status/logs require test_id and no definition or inputs')
        elif self.definition is None or self.test_id:
            raise ValueError('validate/staging require definition and no test_id')
        return self


def tail(value: str) -> str:
    return '\n'.join(value.splitlines()[-15:])[-16000:]


def copy_entry(parent_fd: int, name: str, destination: Path, budget: list[int]) -> None:
    """Copy via no-follow file descriptors: never preserve links or follow devices."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            destination.mkdir(parents=True, exist_ok=True)
            for child in os.listdir(fd):
                copy_entry(fd, child, destination / child, budget)
        elif stat.S_ISREG(info.st_mode):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.dup(fd), 'rb') as source, destination.open('wb') as target:
                while block := source.read(1024 * 1024):
                    budget[0] += len(block)
                    if budget[0] > COPY_LIMIT:
                        raise ValueError('Selected inputs exceed the 50 GiB test-copy limit')
                    target.write(block)
            after = os.fstat(fd)
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                raise ValueError('An input changed during copying; retry with stable inputs')
            destination.chmod(stat.S_IMODE(info.st_mode) | stat.S_IWUSR)
        else:
            raise ValueError('Test inputs must be regular files/directories, not links or devices')
    finally:
        os.close(fd)


def copy_inputs(source: Path, case: Path, inputs: list[PurePosixPath]) -> None:
    budget = [0]
    root_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for relative in inputs:
            parent = os.dup(root_fd)
            try:
                parts = relative.parts
                if not parts:
                    for child in os.listdir(parent):
                        copy_entry(parent, child, case / child, budget)
                else:
                    for part in parts[:-1]:
                        next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        os.close(parent)
                        parent = next_fd
                    copy_entry(parent, parts[-1], case.joinpath(*parts), budget)
            finally:
                os.close(parent)
    finally:
        os.close(root_fd)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


class AssistantWorkflowTestTools:
    def __init__(self, *, settings: Any):
        self.settings = settings
        self.files = AssistantFileTools(settings=settings)

    def build_tools(self, state):
        return [ToolRegistration(
            'tool_workflow_test',
            'Test the actual workflow command on private copies of selected case inputs; the original case is never mounted. '
            'Default mode=staging runs a 10-second startup check. Set timeout_s explicitly when useful; for timeouts over '
            '60 seconds ask the user first unless already explicitly authorized. Timeout means incomplete, never passed. '
            'Returns last 15 lines of stdout/stderr, output checks and a test_id. Longer preparation/execution returns '
            'running; use mode=status with test_id later. mode=logs retrieves full retained stdout/stderr in byte pages '
            '(offset_bytes/max_bytes, capped at 10 MiB text per stream). mode=validate checks schema only. '
            'No command stubs, no automatic rerun, no output registration, no network or GPU. '
            'Do not create/save a separate diagnostic workflow; test the actual draft.',
            _inline_schema(WorkflowTestArgs.model_json_schema()), self.test, parallel_safe=False,
        ).bind(state)]

    def identity(self, state: dict) -> tuple[Path, dict]:
        root = self.files.active_case_root(state)
        context = state.get('context')
        if root is None or context is None:
            raise ValueError('Real-command tests require an authenticated active case')
        return root, {'user_id': str(context.user.id), 'workspace_id': str(state.get('workspace_id')), 'case_id': str(state.get('case_id'))}

    def read_owned(self, state: dict, test_id: str) -> Path:
        _, identity = self.identity(state)
        root = Path(self.settings.fs_data_root).resolve() / ('.workflow-test-' + test_id)
        if root.resolve() != root:
            raise ValueError('Invalid test directory')
        metadata = json.loads((root / 'owner.json').read_text())
        if metadata != identity:
            raise ValueError('Test is not accessible in this user/case context')
        return root

    def status(self, root: Path) -> dict:
        result = json.loads((root / 'result.json').read_text())
        if result['status'] in {'preparing', 'running'} and result['test_id'] not in _TASKS:
            result.update(status='interrupted', error='Application restarted or test task is unavailable; do not assume completion.')
        for stream in ('stdout', 'stderr'):
            path = root / 'logs' / f'{stream}.log'
            if path.exists() and f'{stream}_tail' not in result:
                with path.open('rb') as handle:
                    handle.seek(max(0, path.stat().st_size - 64000))
                    result[f'{stream}_tail'] = tail(handle.read().decode('utf-8', errors='replace'))
            if path.exists():
                result[f'{stream}_log_bytes'] = path.stat().st_size
                result[f'{stream}_log_truncated'] = path.stat().st_size >= LOG_LIMIT
        return result

    async def test(self, state: dict[str, Any], execution: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            parsed = WorkflowTestArgs.model_validate(arguments)
            if parsed.mode == 'validate':
                assert parsed.definition is not None
                return AssistantCatalogTools.config_validate(state, execution, {'definition': parsed.definition.model_dump(by_alias=True)})
            if parsed.mode in {'status', 'logs'}:
                assert parsed.test_id is not None
                root = self.read_owned(state, parsed.test_id)
                if parsed.mode == 'status':
                    result = self.status(root)
                    return ToolResult.structured(result, is_error=result['status'] in {'failed', 'interrupted'})
                path = root / 'logs' / f'{parsed.stream}.log'
                with path.open('rb') as handle:
                    handle.seek(parsed.offset_bytes)
                    data = handle.read(parsed.max_bytes)
                return ToolResult.structured({'test_id': parsed.test_id, 'stream': parsed.stream,
                    'content': data.decode('utf-8', errors='replace'), 'offset_bytes': parsed.offset_bytes,
                    'next_offset_bytes': parsed.offset_bytes + len(data), 'total_bytes': path.stat().st_size,
                    'eof': parsed.offset_bytes + len(data) >= path.stat().st_size,
                    'truncated': path.stat().st_size >= LOG_LIMIT})
            source, identity = self.identity(state)
            tool = parsed.definition
            assert tool is not None
            if len(parsed.inputs) != len(tool.inputs):
                raise ValueError('Provide exactly one ordered selection for each declared input')
            selections = []
            for raw in parsed.inputs:
                value = raw.removeprefix('/case/') if raw.startswith('/case/') else ('.' if raw == '/case' else raw)
                relative = PurePosixPath(value)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Inputs must stay under the active case')
                selections.append(relative)
            test_id = uuid4().hex
            root = Path(self.settings.fs_data_root).resolve() / ('.workflow-test-' + test_id)
            root.mkdir(mode=0o700)
            (root / 'logs').mkdir()
            for stream in ('stdout', 'stderr'):
                (root / 'logs' / f'{stream}.log').touch()
            write_json(root / 'owner.json', identity)
            write_json(root / 'result.json', {'status': 'preparing', 'test_id': test_id, 'timeout_s': parsed.timeout_s,
                                              'case_mounted': False, 'processing': 'real_command'})
            # Release the authorization read transaction before any long await.
            db = state.get('db')
            if isinstance(db, Session) and db.in_transaction():
                db.commit()
            # All DB-dependent authorization is complete before the background task starts.
            task = asyncio.create_task(self.run_test(root, source, selections, tool, parsed.timeout_s))
            _TASKS[test_id] = task
            task.add_done_callback(lambda _: _TASKS.pop(test_id, None))
            await asyncio.wait({task}, timeout=min(parsed.timeout_s + 5, 15))
            result = self.status(root)
            return ToolResult.structured(result, is_error=result['status'] == 'failed')
        except Exception as exc:
            return ToolResult.error(f'Workflow test failed: {exc}')

    async def run_test(self, root: Path, source: Path, selections: list[PurePosixPath], tool: NeuroimagingWorkflow, timeout_s: int) -> None:
        test_id = root.name.removeprefix('.workflow-test-')
        case = root / 'case with spaces'
        case.mkdir()
        result = json.loads((root / 'result.json').read_text())
        started = time.monotonic()
        stopped = False
        submitted = False
        canceled = False
        try:
            await asyncio.to_thread(copy_inputs, source, case, selections)
            inputs = ['/case with spaces' + ('/' + str(path) if path.parts else '') for path in selections]
            prepared = prepare_workflow(tool.id, inputs, RuntimeBind(case, '/case with spaces', 'rw'),
                                        workflow=tool, run_id=test_id, gpu_enabled=False, validation_source_root=source)
            binds = [prepared.bind]
            if prepared.host_output_root is not None:
                prepared.host_output_root.mkdir(parents=True, exist_ok=True)
                binds.append(RuntimeBind(prepared.host_output_root, '/workflow_output/output', 'rw'))
            # Remove only copied declared outputs so pre-existing results cannot masquerade as success.
            for path in prepared.host_outputs:
                if path.is_file():
                    path.unlink()
            container = build_container_request(image=runtime_image_spec(tool.neurodesk_image),
                command=['bash', '-lc', workflow_script(prepared)], binds=binds, scratch_paths=['/workflow_output'],
                env={'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'LC_ALL': 'C'},
                disable_network=True, gpu=False, run_id=test_id)
            container.isolated = True
            container.workflow_test = True
            result['status'] = 'running'
            write_json(root / 'result.json', result)
            submitted = True
            execution = await execute_runtime_request_async(RuntimeExecutionRequest(container_run=container, timeout_s=timeout_s,
                execution_mode='workflow-test', stdout_path=root / 'logs/stdout.log', stderr_path=root / 'logs/stderr.log'))
            stopped = execution.writer_stopped
            result.update(status='completed' if execution.returncode == 0 else 'failed', return_code=execution.returncode,
                          stdout_tail=tail(execution.stdout), stderr_tail=tail(execution.stderr))
            checks = []
            for declaration, path in zip(tool.outputs, prepared.host_outputs, strict=True):
                readable = False
                if path.resolve().is_relative_to(case) and path.is_file() and not path.is_symlink():
                    with path.open('rb') as handle:
                        readable = bool(handle.read(1))
                checks.append({'path': declaration.path, 'required': declaration.required, 'nonempty_readable_file': readable})
            result['outputs'] = checks
            if result['status'] == 'completed' and any(item['required'] and not item['nonempty_readable_file'] for item in checks):
                result.update(status='failed', error='Required outputs are missing, empty or unreadable')
        except RuntimeStoppedTimeout as exc:
            stopped = True
            result.update(status='startup_timeout', error=str(exc), return_code=None,
                          stdout_tail=tail(getattr(exc, 'stdout', '')), stderr_tail=tail(getattr(exc, 'stderr', '')))
        except asyncio.CancelledError:
            canceled = True
            result.update(status='interrupted', error='Test task canceled; completion is unverified')
            raise
        except Exception as exc:
            result.update(status='failed', error=str(exc))
        finally:
            result.update(elapsed_s=round(time.monotonic() - started, 3), writer_stopped=stopped,
                          not_verified=['scientific correctness', 'viewer registration', 'repeat runs'])
            if result['status'] == 'startup_timeout':
                result['not_verified'].append('completion and output correctness')
            write_json(root / 'result.json', result)
            # Never delete a workspace while the bridge may still have a writer.
            if not canceled and (stopped or not submitted):
                await asyncio.to_thread(shutil.rmtree, case)
