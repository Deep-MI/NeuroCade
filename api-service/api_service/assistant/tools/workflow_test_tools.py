"""Synthetic staging tests. No case lookup, case data, or caller-selected mounts."""
from __future__ import annotations

import hashlib
import shlex
import tempfile
from pathlib import Path
from typing import Any, Literal

from neurocade_runtime_tools.container_request import build_container_request
from neurocade_runtime_tools.execution import RuntimeBind, RuntimeExecutionRequest, execute_runtime_request_async
from pydantic import BaseModel, Field

from api_service.assistant.tools.catalog_tools import AssistantCatalogTools, _inline_schema
from api_service.assistant.tools.definition import ToolExecutionContext, ToolResult
from api_service.assistant.tools.registration import ToolRegistration
from api_service.runtime_tools.runtime_images import runtime_image_spec
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow
from api_service.runtime_tools.workflow_execution import prepare_workflow, workflow_script

REQUIRED_FILES = ('mri/norm.mgz', 'mri/wmparc.mgz', 'mri/transforms/talairach.xfm')
TIMEOUT_SECONDS = 30
STREAM_LIMIT = 16384


class WorkflowTestArgs(BaseModel):
    model_config = {"extra": "forbid"}
    definition: NeuroimagingWorkflow
    mode: Literal["validate", "staging"] = "validate"
    fixture: Literal['prepared_subject'] = 'prepared_subject'
    processing_command: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,127}$',
        description='Executable basename to stub. Supported contract: command SUBJECT_NAME SUBJECTS_DIR. '
        'Use a bare command in the workflow (e.g. segmentThalamicNuclei.sh), not an absolute path or command wrapper.')


def fingerprint(root: Path, excluded: set[str]) -> dict[str, str]:
    result = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[0] in excluded:
            continue
        if path.is_symlink():
            result[str(relative)] = 'symlink:' + str(path.readlink())
        elif path.is_file():
            with path.open('rb') as stream:
                result[str(relative)] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def make_fixture(root: Path, folder: str) -> tuple[Path, Path]:
    case = root / 'case with spaces'
    for relative in (*REQUIRED_FILES, 'scripts/recon-all.log', 'surf/lh.white', ('brainstem' if folder != 'brainstem' else 'previous-analysis') + '/mri/unrelated.txt'):
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic input; not medical data\n')
    output = case / folder
    output.mkdir()
    (output / 'keep.txt').write_text('keep existing output\n')
    return case, output


def prepared_script(tool: NeuroimagingWorkflow, case: Path, command: str) -> str:
    """Use the production prologue, then run the exact submitted script."""
    root = '/case with spaces'
    prepared = prepare_workflow(
        tool.id, [root], RuntimeBind(case, root, 'rw'), workflow=tool, run_id='synthetic', gpu_enabled=False,
    )
    # Bash functions work with a noexec /tmp. No model-written stub is evaluated.
    # The stub writes only inside the supplied destination, after checking its inode
    # against OUTPUT_ROOT: aliases are real bind mounts, not symlink approximations.
    stub = f'''function {command}() {{
  [ "$#" -eq 2 ] || {{ echo 'Stub expects SUBJECT_NAME SUBJECTS_DIR' >&2; return 90; }}
  local destination="$2/$1"
  [ "$destination" -ef "$OUTPUT_ROOT" ] || {{ echo 'Processor destination does not match OUTPUT_ROOT' >&2; return 91; }}
'''
    for required in REQUIRED_FILES:
        stub += f'  [ -f "$destination"/{shlex.quote(required)} ] || {{ echo "Missing staged {required}" >&2; return 92; }}\n'
    for item in tool.outputs:
        stub += f'  mkdir -p -- "$destination"/{shlex.quote(str(Path(item.path.replace('{run_id}', 'synthetic')).parent))}\n'
        stub += f'  printf "synthetic processor output\\n" > "$destination"/{shlex.quote(item.path.replace("{run_id}", "synthetic"))}\n'
    stub += '  printf "called\\n" >> "$RUN_DIR/stub-calls"\n}\n'
    stub += f'export -f {command}\n'
    return ('set -euo pipefail\n'
            'ulimit -v 1048576; ulimit -t 20; ulimit -f 65536; ulimit -u 64\n'
            + stub + workflow_script(prepared))


class AssistantWorkflowTestTools:
    def __init__(self, *, settings: Any):
        self.settings = settings

    def build_tools(self, state):
        return [ToolRegistration(
            'tool_workflow_test',
            'mode=validate checks any draft schema/Bash/output paths without execution. mode=staging also tests '
            'directory staging twice in a disposable prepared_subject fixture; requires processing_command, '
            'A harness-owned stub replaces processing_command(SUBJECT_NAME, SUBJECTS_DIR). '
            'Tests real output bind aliases, spaces, input preservation, existing unrelated outputs, and rerun. '
            'No current case or workspace data is mounted or read. No scientific processing or viewer registration is verified. '
            'Staging supports one directory input and a dedicated single-component case_output_folder. '
            'Other workflows can use mode=validate; do not invent probe scripts.',
            _inline_schema(WorkflowTestArgs.model_json_schema()), self.test, parallel_safe=False,
        ).bind(state)]

    async def test(self, state: dict[str, Any], execution: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            parsed = WorkflowTestArgs.model_validate(arguments)
            tool = parsed.definition
            if parsed.mode == "validate":
                return AssistantCatalogTools.config_validate(
                    state, execution, {"definition": tool.model_dump(by_alias=True)})
            if not parsed.processing_command:
                return ToolResult.error("processing_command is required for mode=staging")
            folder = tool.case_output_folder
            if (len(tool.inputs) != 1 or tool.inputs[0].kind != 'directory' or not folder
                    or len(Path(folder).parts) != 1 or folder in {'mri', 'surf', 'label', 'stats', 'scripts', 'touch', '.runs'}):
                return ToolResult.error('Unsupported fixture contract: requires one directory input and a dedicated '
                                        'single-component case_output_folder (not an input subject directory or .runs). '
                                        'Use mode=validate for other workflows.')
            if not any(item.required for item in tool.outputs):
                return ToolResult.error('Declare at least one required output before testing.')
            # The bridge requires binds under its data root. Only this newly allocated
            # child is mounted, never the data root, selected case, or workspace.
            # Neither state nor model arguments contribute a host path.
            with tempfile.TemporaryDirectory(prefix='.workflow-test-', dir=self.settings.fs_data_root) as temporary:
                root = Path(temporary)
                case, output = make_fixture(root, folder)
                baseline = fingerprint(case, {folder, '.runs'})
                script = prepared_script(tool, case, parsed.processing_command)
                attempts = []
                for attempt in range(2):
                    request = build_container_request(
                        image=runtime_image_spec(tool.neurodesk_image), command=['bash', '-lc', script],
                        binds=[RuntimeBind(case, '/case with spaces', 'rw'),
                               RuntimeBind(output, '/workflow_output/output', 'rw')],
                        scratch_paths=['/workflow_output'],
                        env={'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'LC_ALL': 'C'},
                        disable_network=True, gpu=False,
                    )
                    request.isolated = True
                    request.synthetic_fixture = True
                    result = await execute_runtime_request_async(RuntimeExecutionRequest(
                        timeout_s=TIMEOUT_SECONDS, execution_mode='synthetic-workflow-test', container_run=request))
                    failures = []
                    if case.resolve() != case or output.resolve() != output:
                        attempts.append({'attempt': attempt + 1, 'return_code': result.returncode,
                                         'failures': ['fixture_root_replaced'],
                                         'stdout': result.stdout[:STREAM_LIMIT], 'stderr': result.stderr[:STREAM_LIMIT]})
                        break
                    if result.returncode != 0:
                        failures.append('script_failed')
                    after = fingerprint(case, {folder, '.runs'})
                    changed_inputs = sorted(path for path in set(after) | set(baseline) if after.get(path) != baseline.get(path))
                    if changed_inputs:
                        failures.append('input_or_unrelated_files_changed')
                    keep = output / 'keep.txt'
                    if not keep.is_file() or keep.is_symlink() or keep.read_text() != 'keep existing output\n':
                        failures.append('existing_output_changed')
                    unexpected_paths = [name for name in (folder, 'brainstem' if folder != 'brainstem' else 'previous-analysis',
                                                         'mri/mri', 'surf/surf', 'scripts/scripts') if (output / name).exists()]
                    if unexpected_paths:
                        failures.append('recursive_or_unrelated_copy')
                    marker = case / '.runs/synthetic/stub-calls'
                    if not marker.is_file() or marker.resolve() != marker or marker.read_text().splitlines() != ['called'] * (attempt + 1):
                        failures.append('processor_stub_not_called_once')
                    for item in tool.outputs:
                        path = output / item.path.replace("{run_id}", "synthetic")
                        if item.required and (not path.is_file() or path.is_symlink()
                                or not path.resolve().is_relative_to(output.resolve())
                                or path.read_text() != 'synthetic processor output\n'):
                            failures.append('missing_or_incorrect_output:' + item.path)
                    attempts.append({'attempt': attempt + 1, 'return_code': result.returncode, 'failures': failures,
                                     'stdout': result.stdout[:STREAM_LIMIT], 'stderr': result.stderr[:STREAM_LIMIT],
                                     'changed_input_paths': changed_inputs, 'unexpected_output_paths': unexpected_paths,
                                     'preserve_existing_output': 'keep.txt'})
                    if failures:
                        break
                    if attempt == 0:
                        for item in tool.outputs:
                            path = output / item.path.replace("{run_id}", "synthetic")
                            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(output.resolve()):
                                path.unlink()
                passed = len(attempts) == 2 and all(not item['failures'] for item in attempts)
                return ToolResult.structured({
                    'status': 'staging_passed' if passed else 'staging_failed', 'attempts': attempts,
                    'case_mounted': False, 'fixture': parsed.fixture, 'fixture_required_files': list(REQUIRED_FILES), 'processing': 'stubbed',
                    'not_verified': ['CLI compatibility', 'real inputs', 'scientific outputs', 'viewer registration'],
                }, is_error=not passed)
        except Exception as exc:
            return ToolResult.error(f'Synthetic workflow test failed: {exc}')
