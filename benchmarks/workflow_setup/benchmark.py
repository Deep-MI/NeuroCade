"""Offline workflow setup benchmark. Never connects to a NeuroCade app or bridge."""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import yaml
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow
from nibabel.filebasedimages import ImageFileError
from nibabel.loadsave import load, save
from nibabel.nifti1 import Nifti1Image
from nibabel.spatialimages import SpatialImage

SUBJECT_DIRS = ('mri', 'surf', 'label', 'stats', 'scripts', 'touch')
REQUIRED = ('mri/norm.mgz', 'mri/wmparc.mgz', 'mri/transforms/talairach.xfm')


def snapshot(root: Path, excluded: tuple[str, ...] = ()) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if relative.parts[0] in excluded:
            continue
        if path.is_symlink():
            raise ValueError(f'Symlinks are not supported in benchmark fixtures: {relative}')
        if path.is_file():
            with path.open('rb') as stream:
                result[str(relative)] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def prepare(root: Path, task: str, subject: Path | None = None) -> Path:
    """Create fixtures only in a new directory; copy subject data without linking."""
    root.mkdir(exist_ok=False, parents=True)
    case = root / 'case with spaces'
    case.mkdir()
    if task == 'mean':
        data = np.random.default_rng(20261014).integers(-100, 500, size=(5, 6, 7, 4)).astype(np.float32)
        affine = np.array([[0, -2, 0, 20], [1.5, 0, 0, -30], [0, 0, 3, 10], [0, 0, 0, 1]])
        save(Nifti1Image(data, affine), case / 'input series.nii.gz')
    else:
        if subject is None:
            raise ValueError('Thalamus requires an explicitly selected, prepared subject')
        for relative in REQUIRED:
            if not (subject / relative).is_file():
                raise ValueError(f'Missing subject prerequisite: {relative}')
        # Reject links rather than following them out of the selected fixture.
        for directory in SUBJECT_DIRS:
            source = subject / directory
            if source.exists():
                if source.is_symlink():
                    raise ValueError(f'Subject directory is a symlink: {directory}')
                snapshot(source)
                shutil.copytree(source, case / directory)
        # Remove prior target results only from the disposable copy to prevent false passes.
        for pattern in ('mri/ThalamicNuclei*', 'mri/ThalamicNucleiVolumes*', 'stats/thalamic-nuclei*'):
            for path in case.glob(pattern):
                if path.is_file():
                    path.unlink()
    (case / 'brainstem' / 'mri').mkdir(parents=True)
    (case / 'brainstem' / 'mri' / 'unrelated.txt').write_text('previous unrelated result\n')
    output = case / ('mean' if task == 'mean' else 'thalamus')
    output.mkdir()
    (output / 'keep.txt').write_text('existing output must survive\n')
    (root / 'baseline.json').write_text(json.dumps(snapshot(case, (output.name, '.runs')), indent=2))
    (root / 'task.json').write_text(json.dumps({'task': task, 'output': output.name}))
    return case


def valid_volume_report(path: Path) -> bool:
    """Check numeric report rows, not anatomical accuracy or volume agreement."""
    if not path.is_file():
        return False
    rows = []
    try:
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            fields = line.split()
            # FreeSurfer volumes text: structure name followed by volume.
            # FreeSurfer stats: index, segid, voxel count, volume, structure.
            if path.suffix == '.txt' and len(fields) == 2:
                name, volume = fields
            elif path.suffix == '.stats' and len(fields) >= 5:
                name, volume = fields[4], fields[3]
            else:
                return False
            value = float(volume)
            if not any(char.isalpha() for char in name) or not np.isfinite(value) or value < 0:
                return False
            rows.append(value)
    except (OSError, UnicodeError, ValueError):
        return False
    return bool(rows) and any(value > 0 for value in rows)


def grade(root: Path, workflow: NeuroimagingWorkflow) -> dict:
    task = json.loads((root / 'task.json').read_text())
    case = root / 'case with spaces'
    output = case / task['output']
    failures = []
    if snapshot(case, (output.name, '.runs')) != json.loads((root / 'baseline.json').read_text()):
        failures.append('input_or_unrelated_files_changed')
    if not (output / 'keep.txt').is_file() or (output / 'keep.txt').read_text() != 'existing output must survive\n':
        failures.append('existing_output_changed')
    if any((output / name).exists() for name in ('brainstem', output.name)):
        failures.append('recursive_or_unrelated_copy')
    wanted = 'intensity_volume' if task['task'] == 'mean' else 'segmentation_volume'
    primary = [item for item in workflow.outputs if item.type == wanted and item.required]
    if len(primary) != 1:
        failures.append('exactly_one_required_primary_output_expected')
    for item in workflow.outputs:
        if item.required and not (output / item.path).is_file():
            failures.append(f'missing_required_output:{item.path}')
    if len(primary) == 1 and (output / primary[0].path).is_file():
        item = primary[0]
        try:
            image = load(output / item.path)
            if not isinstance(image, SpatialImage):
                raise ValueError("Output is not a spatial volume")
            data = image.get_fdata()
            if task['task'] == 'mean':
                source = load(case / 'input series.nii.gz')
                if not isinstance(source, SpatialImage):
                    raise ValueError('Input is not a spatial volume')
                expected = source.get_fdata().mean(axis=3)
                if data.shape != expected.shape or not np.allclose(data, expected, rtol=0, atol=1e-5):
                    failures.append('incorrect_temporal_mean')
                if image.affine is None or source.affine is None or not np.allclose(image.affine, source.affine, rtol=0, atol=1e-5):
                    failures.append('geometry_changed')
            else:
                labels = np.unique(data)
                if (data.ndim != 3 or not np.isfinite(data).all() or np.any(data < 0)
                        or not np.any(data == 0) or np.count_nonzero(labels > 0) < 2
                        or not np.equal(data, np.floor(data)).all()):
                    failures.append('invalid_label_volume')
                if item.metadata.get('lut') != 'freesurfer':
                    failures.append('missing_freesurfer_lut')
                reports = [output / entry.path for entry in workflow.outputs
                           if entry.required and entry.type == 'other'
                           and (Path(entry.path).match('mri/ThalamicNucleiVolumes*.txt')
                                or Path(entry.path).match('stats/thalamic-nuclei*.stats'))]
                if not reports or not all(valid_volume_report(path) for path in reports):
                    failures.append('missing_or_invalid_required_volumes_report')
        except (OSError, ValueError, ImageFileError) as exc:
            failures.append(f'unreadable_output:{exc}')
    return {'passed': not failures, 'failures': failures,
            'not_verified': ['live artifact registration', 'viewer behavior', 'scientific segmentation accuracy', 'agent tool use']}


def run(workflow_path: Path, task: str, subject: Path | None, license_path: Path | None, timeout: int) -> Path:
    workflow = NeuroimagingWorkflow.model_validate(yaml.safe_load(workflow_path.read_text()))
    folder = 'mean' if task == 'mean' else 'thalamus'
    if workflow.case_output_folder != folder or len(workflow.inputs) != 1:
        raise ValueError(f'Benchmark requires one input and case_output_folder={folder!r}')
    if workflow.inputs[0].kind != ('file' if task == 'mean' else 'directory'):
        raise ValueError('Incorrect input kind')
    # Never pull images, build/restart the app, or contact its API/bridge.
    image_info = subprocess.run(['docker', 'image', 'inspect', workflow.image], check=True, capture_output=True, text=True)
    image_id = json.loads(image_info.stdout)[0]['Id']
    root = Path(tempfile.mkdtemp(prefix='neurocade-benchmark-'))
    case = prepare(root / 'fixture', task, subject)
    root = root / 'fixture'
    input_path = '/case/input series.nii.gz' if task == 'mean' else '/case'
    outputs = ' '.join(shlex.quote('/workflow_output/output/' + item.path) for item in workflow.outputs)
    program = '\n'.join([
        'set -euo pipefail',
        f'readonly -a INPUTS=({shlex.quote(input_path)})', f'readonly -a OUTPUTS=({outputs})',
        'readonly CASE_ROOT=/case DEVICE=cpu RUN_DIR=/case/.runs/benchmark',
        'readonly OUTPUT_ROOT=/workflow_output/output OUTPUT_PARENT=/workflow_output OUTPUT_NAME=output',
        'mkdir -p "$RUN_DIR"',
        *[f'mkdir -p {shlex.quote("/workflow_output/output/" + str(Path(item.path).parent))}' for item in workflow.outputs],
        workflow.script,
    ])
    (root / 'workflow.yaml').write_text(workflow_path.read_text())
    (root / 'script.sh').write_text(program)
    records = []
    for attempt in range(2):
        name = f'neurocade-benchmark-{root.parent.name.removeprefix("neurocade-benchmark-")}-{attempt}'
        command = ['docker', 'run', '--rm', '--pull=never', '--name', name, '--network=none',
                   '--cpus=2', '--memory=8g', '--pids-limit=256', '--read-only',
                   '--tmpfs', '/tmp:rw,nosuid,nodev,exec', '--entrypoint', '/bin/bash',
                   '-v', f'{case}:/case:rw', '-v', f'{case / folder}:/workflow_output/output:rw']
        if license_path:
            command += ['-v', f'{license_path.resolve(strict=True)}:/benchmark-license.txt:ro', '-e', 'FS_LICENSE=/benchmark-license.txt']
        command += [image_id, '-c', program]
        started = time.monotonic()
        try:
            with (root / f'{attempt}.stdout').open('w') as stdout, (root / f'{attempt}.stderr').open('w') as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
            record = {'returncode': result.returncode, 'elapsed_s': time.monotonic() - started, **grade(root, workflow)}
            record['passed'] = record['passed'] and result.returncode == 0
        except subprocess.TimeoutExpired:
            record = {'passed': False, 'failures': ['timeout'], 'elapsed_s': time.monotonic() - started}
        finally:
            # Only this benchmark's uniquely named container can be removed.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)
        records.append(record)
        (root / 'results.json').write_text(json.dumps({'image_id': image_id, 'task': task, 'attempts': records}, indent=2))
        if not record['passed']:
            break
        # Keep staging/existing-output clutter but remove declared products so the rerun
        # must produce fresh outputs; an exit-zero no-op must not pass on stale files.
        if attempt == 0:
            for item in workflow.outputs:
                path = case / folder / item.path
                if path.is_file():
                    path.unlink()
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', choices=['mean', 'thalamus'])
    parser.add_argument('workflow', type=Path)
    parser.add_argument('--subject', type=Path)
    parser.add_argument('--license', type=Path)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    results = run(args.workflow, args.task, args.subject, args.license, args.timeout)
    print(results)
    report = json.loads((results / 'results.json').read_text())
    raise SystemExit(0 if len(report['attempts']) == 2 and all(item['passed'] for item in report['attempts']) else 1)
