# Workflow setup benchmark

Two tasks for comparing agent workflow authoring before/after harness changes.
This standalone runner never connects to the app/bridge, registers
outputs, or changes the live catalog. Each execution creates a fresh temporary
case. It accepts a workflow YAML produced by the agent; it does **not** drive an
LLM itself. Keep agent transcripts separately when comparing harness versions.

## Current agent prompts

Use `prompts.json` verbatim, without appending benchmark guidance:

> Set up FSL-Maths Gaussian blur with kernel size 5 millimeters.

> Set up segment thalamic nuclei from the latest FreeSurfer version.

The Gaussian-blur task replaces temporal mean for future agent trials. The
standalone runner below still grades the historical temporal-mean task; its
numeric grader must not be used to score Gaussian blur.

## Reproducible real-data preparation

From the repository root, use the locked project environment:

```bash
uv sync --locked --extra test
uv run python benchmarks/workflow_setup/prepare_fixtures.py \
  --cache "$HOME/.cache/neurocade/workflow-benchmark" \
  --output /tmp/neurocade-benchmark-fixtures-001
```

Choose a **new output directory** on each run. The command downloads two pinned
archives, checks their published checksums (also on cache reuse), and creates:

- `blur/001.nii.gz`: the release sample's `mri/001.mgz` converted with NiBabel,
  without resampling. Values, shape, voxel spacing and affine are verified after
  saving. This is a temporary benchmark convenience, not a harness capability.
- `thalamus/`: one complete prepared FreeSurfer subject from the processed
  Rhineland archive. Subject directories are copied without unrelated pipelines;
  old thalamus products and running markers are excluded. Required volumes must
  be readable and share geometry; the Talairach transform must be present.
- `manifest.json`: source URLs/checksums, archive subject path, conversion details,
  dependency versions, exact prompts, and SHA-256 hashes of every prepared file.

Sources are the NeuroCade `v2026.6.7` sample release and
[Zenodo record 19133592](https://zenodo.org/records/19133592), specifically
`sub_rs_mri_proc.zip`, whose prepared subject is under
`sub_rs_mri_proc/freesurfer6_pipeline/` (6.6 GB download, published MD5
`5a300bc10b8cf09b7799a2e3087c3220`). Allow at least 15 GB free for downloads,
extraction and trial copies. No raw MRI archive is needed. Interrupted downloads
are not published as cached archives; retry the command. Downloads are performed
only by this preparation command, never by the agent or live application.
An offline developer can place the exact archives in `--cache`; checksums still
apply. The archive URLs and checksums are constants in `prepare_fixtures.py`.

For each agent trial, copy the selected `blur/` or `thalamus/` fixture into a fresh
case in an **isolated benchmark database/data root**, register the copied files
using normal artifact classification, and send the corresponding `prompts.json`
entry unchanged. Never point the benchmark at the production app/bridge or import
fixtures into an everyday case. The staging tool will make its own disposable
copy, as it does in normal use. Preserve the fixture manifest with the transcript.
The preparation command itself does not start an agent, Docker, or the app.

Compare trials using the same manifest and model settings. Report schema validity,
startup-test outcome, approval request, and full processing separately. A startup
timeout is not a segmentation pass. The published subject was processed with
FreeSurfer 6; running the latest segmentation on it still requires verification.
The older temporal-mean numeric grader below does not grade Gaussian blur.

Offline regression checks:

```bash
uv run pytest tests/test_workflow_benchmark_fixtures.py -q
```

## Historical tasks and prompts

**Easy — FSL temporal mean**

> Create a workflow using fslmaths -Tmean for input series.nii.gz. Save a required
> 3D intensity output under the case's mean folder, retaining spatial geometry.
> Preserve all inputs and unrelated files. The workflow must work again when
> its output folder already exists. Return the complete workflow YAML.

The input is seeded pseudorandom 5×6×7×4 data (including negative values) with nontrivial orientation and spacing.
The grader checks every voxel against the known temporal mean and checks affine
geometry. An exit code alone is insufficient.

**Difficult — FreeSurfer thalamic nuclei**

> Create a CPU workflow running segmentThalamicNuclei.sh on the selected prepared
> subject directory. Stage the files it requires into thalamus without modifying
> the source. The case includes an unrelated brainstem output and an existing
> thalamus folder. Declare the primary segmentation as required, with FreeSurfer
> LUT metadata, and include the volumes report. Return the complete workflow YAML.

Supply an explicit **offline copy** of a prepared subject, not an actively changing
case. The runner copies only subject directories into another disposable case,
rejects symlinks, and removes old thalamic results from that copy. It tests spaces
in paths and real overlapping Docker bind mounts (`/case/thalamus` and
`/workflow_output/output`), not symlink approximations. It checks input hashes,
existing-output preservation, missing/invalid results, LUT declaration, required volumes
report declarations and finite nonnegative numeric rows, and accidental copying of unrelated or recursive output folders.
The label check is structural, not a scientific quality assessment.

## Run

Use the repository Python environment and PYTHONPATH:

```bash
export PYTHONPATH=.:api-service:packages/neurocade-runtime-tools/src
python benchmarks/workflow_setup/benchmark.py mean /path/to/candidate.yaml
python benchmarks/workflow_setup/benchmark.py thalamus /path/to/candidate.yaml \
  --subject /path/to/offline-subject --license /path/to/license.txt --timeout 3600
pytest tests/test_workflow_setup_benchmark.py -q
```

Install/cache the candidate's versioned container image separately. The runner
uses `docker image inspect` and executes its immutable local image ID with
`--pull=never`; image download time is excluded. It uses no network, caps each
container at 2 CPUs/8 GiB, and only mounts disposable case data and an optional
read-only license. `/tmp` is executable for MATLAB. These containers still share
host resources: defer real runs while an interactive analysis needs them.

Each candidate runs twice. Declared products are removed before the second run
so stale files cannot masquerade as fresh success; staged directories remain.
A failed first attempt stops the trial. Temporary fixtures, candidate YAML,
script, stdout/stderr and `results.json` are retained at the printed location.
Timeout cleanup removes only the unique benchmark container. There is no automatic
retry, image pull, database, GUI, production data write, or app restart.

## Compare harness versions

Use the same model/settings, exact prompt, image ID and subject copy with a fresh
conversation and fixture for each trial. Record: completion without correction,
number of tool calls and failed calls, authoring time, validation/approval count,
first-run and rerun results, and whether the final agent report accurately states
what ran. Use several trials rather than treating one success as a reliable rate.
Report cold image setup separately. Thalamus is a regression case for the staging
failure already seen, not evidence of generalization to unfamiliar software.

The runner checks declared output types/metadata; **actual artifact registration,
viewer loading, and agent tool behavior are not evaluated here**. Those require a
separately provisioned app and bridge before using the live evaluation helpers.
Do not point existing live tests at your everyday app for this benchmark.

References: [FSL temporal operations](https://pages.fmrib.ox.ac.uk/fsl/fslpy/_modules/fsl/wrappers/fslmaths.html),
[FreeSurfer thalamic nuclei](https://freesurfer.net/fswiki/ThalamicNuclei).
The installed image's CLI and filenames are authoritative when versions differ.

## Benchmark validation findings (2026-10-14)

No live agent success rate or real FSL/FreeSurfer processing result has been
measured yet. The initial seven tests validated the runner, not agent capability.
Two new negative controls reproduced false passes in that initial grader:

- A temporal median passed because the linear input made median equal mean.
  Seeded nonuniform data now distinguishes them while keeping an exact mean.
- A constant all-ones segmentation plus a junk report passed. The structural
  check now requires background and multiple positive integer labels; reports
  must be declared required and contain numeric volume rows. Required reports
  are removed before rerun, so a stale report cannot satisfy the second attempt.

All 15 offline tests now pass, including these negative controls. This strengthens
failure detection; it does not establish anatomical accuracy. The grader does not
validate the exact thalamic label set or reconcile reported volumes with image
voxels (partial-volume estimates need not equal hard-label voxel counts).
