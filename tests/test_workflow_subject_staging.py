"""Exercise the repaired user workflow with Bash, without running segmentation."""
import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow, workflow_output_path


@pytest.mark.parametrize("select_parent", [False, True])
def test_segmentbs_stages_subject_without_recursive_case_copy(tmp_path, select_parent):
    definition = yaml.safe_load((Path(__file__).parent / "fixtures/workflows/freesurfer_segmentbs.yaml").read_text())
    workflow = NeuroimagingWorkflow.model_validate(definition)
    case = tmp_path / "case with spaces"
    source = case / "subject with spaces" if select_parent else case
    for relative in ("mri/wmparc.mgz", "mri/norm.mgz", "mri/transforms/talairach.xfm", "scripts/IsRunningBSsubst"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    output = case / "brainstem"
    output.mkdir()
    # Existing output must not be mistaken for a second subject on retry.
    if not select_parent:
        (output / "mri").mkdir()
    (case / "unrelated-upload.txt").write_text("do not copy")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "segmentBS.sh"
    stub.write_text('#!/bin/bash\nset -eu\ndest="$2/$1"\ntest -f "$dest/mri/wmparc.mgz"\ntest ! -e "$dest/scripts/IsRunningBSsubst"\necho result > "$dest/mri/brainstemSsLabels.v13.mgz"\n')
    stub.chmod(0o755)
    program = f"INPUTS=({shlex.quote(str(case))})\n" + workflow.script
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "OUTPUT_ROOT": str(output), "OUTPUT_PARENT": str(case), "OUTPUT_NAME": "brainstem"}
    for _ in range(2):
        subprocess.run(["bash", "-euo", "pipefail", "-c", program], env=env, check=True, capture_output=True, text=True)
    relative = workflow_output_path(workflow, workflow.outputs[0].path, run_id="test")
    assert relative == "brainstem/mri/brainstemSsLabels.v13.mgz"
    assert (case / relative).read_text().strip() == "result"
    assert not (output / "unrelated-upload.txt").exists()
    assert not (output / "brainstem").exists()
    assert (source / "scripts/IsRunningBSsubst").read_text() == "original"
    assert (source / "mri/norm.mgz").read_text() == "original"
