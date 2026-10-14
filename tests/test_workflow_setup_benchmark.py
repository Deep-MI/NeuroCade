"""Offline benchmark oracles: no application, bridge, Docker, or LLM access."""
import importlib.util
import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow

SPEC = importlib.util.spec_from_file_location('workflow_benchmark', Path(__file__).parents[1] / 'benchmarks/workflow_setup/benchmark.py')
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def workflow(task='mean'):
    return NeuroimagingWorkflow.model_validate({
        'id': 'benchmark', 'image': 'example/tool:1.0', 'description': 'Benchmark candidate', 'details': 'Offline fixture',
        'inputs': [{'name': 'input', 'description': 'Input', 'kind': 'file' if task == 'mean' else 'directory'}],
        'outputs': [{'name': 'result', 'type': 'intensity_volume' if task == 'mean' else 'segmentation_volume',
                     'path': 'result.nii.gz', 'description': 'Result', 'required': True,
                     'metadata': {'lut': 'freesurfer'} if task != 'mean' else {}}],
        'case_output_folder': 'mean' if task == 'mean' else 'thalamus', 'script': 'echo candidate',
        'execution': {'gpu': False},
    })


def test_mean_oracle_rejects_wrong_values_geometry_and_mutation(tmp_path):
    root = tmp_path / 'fixture'
    case = benchmark.prepare(root, 'mean')
    source = nib.load(case / 'input series.nii.gz')
    correct = source.get_fdata().mean(axis=3)
    output = case / 'mean/result.nii.gz'
    nib.save(nib.Nifti1Image(correct, source.affine), output)
    assert benchmark.grade(root, workflow())['passed']
    nib.save(nib.Nifti1Image(correct + 1, np.eye(4)), output)
    failures = benchmark.grade(root, workflow())['failures']
    assert 'incorrect_temporal_mean' in failures and 'geometry_changed' in failures
    (case / 'brainstem/mri/unrelated.txt').write_text('changed')
    assert 'input_or_unrelated_files_changed' in benchmark.grade(root, workflow())['failures']


def test_missing_optional_output_cannot_pass(tmp_path):
    root = tmp_path / 'fixture'
    benchmark.prepare(root, 'mean')
    candidate = workflow()
    candidate.outputs[0].required = False
    assert not benchmark.grade(root, candidate)['passed']


def test_thalamus_fixture_is_independent_and_rejects_recursive_copy(tmp_path):
    source = tmp_path / 'source'
    for relative in benchmark.REQUIRED:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('immutable source')
    (source / 'mri/ThalamicNuclei.v13.T1.mgz').write_text('stale result')
    before = benchmark.snapshot(source)
    root = tmp_path / 'fixture'
    case = benchmark.prepare(root, 'thalamus', source)
    assert not (case / 'mri/ThalamicNuclei.v13.T1.mgz').exists()
    assert benchmark.snapshot(source) == before
    nib.save(nib.Nifti1Image(np.ones((3, 4, 5), dtype=np.int16), np.eye(4)), case / 'thalamus/result.nii.gz')
    (case / 'thalamus/stats').mkdir()
    (case / 'thalamus/stats/thalamic-nuclei.lh.v13.T1.stats').write_text('test report')
    assert benchmark.grade(root, workflow('thalamus'))['passed']
    (case / 'thalamus/thalamus').mkdir()
    assert 'recursive_or_unrelated_copy' in benchmark.grade(root, workflow('thalamus'))['failures']
    (case / 'mri/norm.mgz').write_text('mutated fixture')
    assert benchmark.snapshot(source) == before


def test_existing_fixture_and_subject_symlinks_rejected(tmp_path):
    with pytest.raises(FileExistsError):
        benchmark.prepare(tmp_path, 'mean')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'external').symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError, match='Symlinks'):
        benchmark.snapshot(source)


def test_runner_uses_disposable_bind_mounts_and_stops_on_failure(tmp_path, monkeypatch):
    import subprocess

    calls = []
    original_run = subprocess.run
    monkeypatch.setattr(benchmark.tempfile, 'mkdtemp', lambda **kwargs: str(tmp_path / 'run'))
    def fake_run(command, **kwargs):
        if command[0] != 'docker':
            return original_run(command, **kwargs)
        calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([{'Id': 'sha256:pinned'}]))
        return subprocess.CompletedProcess(command, 1)
    monkeypatch.setattr(benchmark.subprocess, 'run', fake_run)
    candidate = tmp_path / 'candidate.yaml'
    candidate.write_text(benchmark.yaml.safe_dump(workflow().model_dump(by_alias=True)))
    root = benchmark.run(candidate, 'mean', None, None, 30)
    executions = [call for call in calls if call[:2] == ['docker', 'run']]
    assert len(executions) == 1
    assert '--pull=never' in executions[0] and '--network=none' in executions[0]
    assert f'{root / "case with spaces"}:/case:rw' in executions[0]
    assert f'{root / "case with spaces/mean"}:/workflow_output/output:rw' in executions[0]
    assert json.loads((root / 'results.json').read_text())['attempts'][0]['passed'] is False


@pytest.mark.parametrize('timeout', [False, True])
def test_runner_fresh_rerun_and_timeout_cleanup(tmp_path, monkeypatch, timeout):
    import subprocess

    original_run = subprocess.run
    runs = []
    cleanups = []
    def fake_run(command, **kwargs):
        if command[0] != 'docker':
            return original_run(command, **kwargs)
        if command[1] == 'image':
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([{'Id': 'sha256:pinned'}]))
        if command[1] == 'rm':
            cleanups.append(command[-1])
            return subprocess.CompletedProcess(command, 0)
        runs.append(command)
        if timeout:
            raise subprocess.TimeoutExpired(command, 30)
        case = Path(command[command.index('-v') + 1].removesuffix(':/case:rw'))
        result = case / 'mean/result.nii.gz'
        assert not result.exists(), 'Stale output must be removed before rerun'
        source = nib.load(case / 'input series.nii.gz')
        nib.save(nib.Nifti1Image(source.get_fdata().mean(axis=3), source.affine), result)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(benchmark.subprocess, 'run', fake_run)
    monkeypatch.setattr(benchmark.tempfile, 'mkdtemp', lambda **kwargs: str(tmp_path / 'run'))
    candidate = tmp_path / 'candidate.yaml'
    candidate.write_text(benchmark.yaml.safe_dump(workflow().model_dump(by_alias=True)))
    root = benchmark.run(candidate, 'mean', None, None, 30)
    records = json.loads((root / 'results.json').read_text())['attempts']
    assert len(records) == (1 if timeout else 2)
    assert all(record['passed'] is not timeout for record in records)
    assert len(cleanups) == len(runs)
    assert all(name.startswith('neurocade-benchmark-') for name in cleanups)
