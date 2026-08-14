"""Tests for the authoritative neuroimaging workflow catalog."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from neurocade_runtime_tools.container_request import RuntimeBind
from neurocade_runtime_tools.execution import RuntimeExecutionResult

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime import neuroimaging_tasks as neuroimaging_tasks_module  # noqa: E402
from api_service.runtime_tools import prepare_images as prepare_images_module  # noqa: E402
from api_service.runtime_tools import workflow_execution as execution_module  # noqa: E402
from api_service.runtime_tools.workflow_catalog import (  # noqa: E402
    WorkflowExecution,
    delete_user_workflow,
    inspect_workflow,
    load_workflow_catalog,
    resolve_workflow,
    run_analysis_workflows_payload,
    search_workflows,
    upsert_user_workflow,
    user_workflow_catalog_path,
    workflow_source,
)

from backend_common.db import Run  # noqa: E402


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    load_workflow_catalog.cache_clear()
    yield
    load_workflow_catalog.cache_clear()


def test_default_catalog_has_fixed_workflows_and_defaults():
    catalog = load_workflow_catalog()
    tools = {tool.id: tool for tool in catalog.tools}
    defaults = WorkflowExecution()

    assert set(tools) == {
        "mri_info",
        "mri_info_resolution",
        "fsqc",
        "fastsurfer_full",
        "fastsurfer_segmentation",
        "fastsurfer_fast",
    }
    assert tools["mri_info"].execution.timeout_s is None
    assert tools["mri_info"].execution.mode == "synchronous"
    assert tools["mri_info"].execution.gpu is False
    assert tools["fsqc"].image == "fsqc_2.1.4:20251126"
    assert tools["fsqc"].execution.mode == "synchronous"
    assert tools["fsqc"].execution.gpu is False
    assert tools["fastsurfer_full"].neurodesk_image == "deepmi/fastsurfer:cu128-v2.5.4"
    assert tools["fastsurfer_full"].execution.gpu is True
    assert tools["fastsurfer_full"].execution.mode == "background"
    for tool_id in ("fastsurfer_full", "fastsurfer_segmentation", "fastsurfer_fast"):
        assert all(output.path != "." for output in tools[tool_id].outputs)
        assert all(output.type in {"intensity_volume", "segmentation_volume", "surface", "other"} for output in tools[tool_id].outputs)
        assert '--sd "${subjects_dir}"' in tools[tool_id].script
        assert '--sid "${subject_id}"' in tools[tool_id].script
        assert "${RUN_DIR}/subjects" not in tools[tool_id].script
        assert "cp " not in tools[tool_id].script
    assert {output.type for output in tools["fastsurfer_full"].outputs} == {
        "intensity_volume",
        "segmentation_volume",
        "surface",
        "other",
    }
    full_outputs = {output.path: output for output in tools["fastsurfer_full"].outputs}
    segmentation_outputs = {output.name: output for output in tools["fastsurfer_segmentation"].outputs}
    assert full_outputs["stats/aseg+DKT.VINN.stats"].name == "aseg_statistics"
    assert segmentation_outputs["aseg_statistics"].path == "stats/aseg+DKT.VINN.stats"
    assert full_outputs["mri/callosum.CC.orig.mgz"].type == "segmentation_volume"
    assert full_outputs["mri/callosum.CC.upright.mgz"].type == "segmentation_volume"
    assert full_outputs["surf/callosum.surf"].type == "surface"
    assert defaults.timeout_s is None
    assert defaults.mode == "synchronous"
    assert defaults.gpu is True
    assert "--seg_only" not in tools["fastsurfer_full"].script
    assert "--seg_only" in tools["fastsurfer_segmentation"].script
    assert {"--seg_only", "--no_biasfield", "--no_cereb", "--no_hypothal"}.issubset(
        set(tools["fastsurfer_fast"].script.split())
    )
    assert "license" not in ROOT.joinpath("config/neuroimaging_tools.yaml").read_text(encoding="utf-8").lower()
    assert not ROOT.joinpath("config/runtime_tools.json").exists()
    assert not ROOT.joinpath(
        "packages/neurocade-runtime-tools/src/neurocade_runtime_tools/container_specs.py"
    ).exists()


def test_search_payload_is_compact_and_inspect_is_lazy():
    payload = run_analysis_workflows_payload()
    assert [tool["id"] for tool in payload] == [
        "mri_info",
        "mri_info_resolution",
        "fsqc",
        "fastsurfer_full",
        "fastsurfer_segmentation",
        "fastsurfer_fast",
    ]
    assert {tool["input_artifact_kind"] for tool in payload} == {"intensity_volume"}
    inspected = inspect_workflow("mri_info")
    assert "details" in inspected
    assert inspected["image"] == "freesurfer_8.1.0:20260311"
    assert inspected["inputs"][0]["name"] == "volume"
    assert "script" not in inspected


def test_user_workflow_overlays_are_isolated_and_reload_immediately(tmp_path):
    settings = SimpleNamespace(outputs_dir=tmp_path / "output")
    base = resolve_workflow("mri_info").model_dump(mode="json", by_alias=True, exclude_none=True)
    first = {**base, "description": "First user's private MRI information workflow."}
    second = {**base, "description": "Second user's private MRI information workflow."}

    upsert_user_workflow(settings, "user/one", first)
    upsert_user_workflow(settings, "user:two", second)

    assert resolve_workflow("mri_info").description == base["description"]
    assert resolve_workflow("mri_info", settings=settings, user_id="user/one").description == first["description"]
    assert resolve_workflow("mri_info", settings=settings, user_id="user:two").description == second["description"]
    assert workflow_source("mri_info", settings=settings, user_id="user/one") == "user_override"
    assert user_workflow_catalog_path(settings, "user/one").parent == settings.outputs_dir / ".user-tool-configs"
    assert "user/one" not in str(user_workflow_catalog_path(settings, "user/one"))

    delete_user_workflow(settings, "user/one", "mri_info")

    assert resolve_workflow("mri_info", settings=settings, user_id="user/one").description == base["description"]
    assert workflow_source("mri_info", settings=settings, user_id="user/one") == "built_in"
    assert resolve_workflow("mri_info", settings=settings, user_id="user:two").description == second["description"]


def test_user_workflow_upsert_rejects_invalid_definition_without_changing_overlay(tmp_path):
    settings = SimpleNamespace(outputs_dir=tmp_path / "output")
    definition = resolve_workflow("mri_info").model_dump(mode="json", by_alias=True, exclude_none=True)
    definition["description"] = "Valid private definition."
    upsert_user_workflow(settings, "user-1", definition)
    path = user_workflow_catalog_path(settings, "user-1")
    original = path.read_bytes()

    invalid = {**definition, "image": "freesurfer:latest"}
    with pytest.raises(ValueError, match="explicit non-latest tag"):
        upsert_user_workflow(settings, "user-1", invalid)

    assert path.read_bytes() == original
    assert resolve_workflow("mri_info", settings=settings, user_id="user-1").description == definition["description"]


def test_user_created_workflow_appears_only_in_its_owner_search(tmp_path):
    settings = SimpleNamespace(outputs_dir=tmp_path / "output")
    definition = resolve_workflow("mri_info_resolution").model_dump(mode="json", by_alias=True, exclude_none=True)
    definition.update(
        id="private_voxel_report",
        description="Private voxel report for a single user.",
        details="Report private voxel spacing.",
    )
    upsert_user_workflow(settings, "owner", definition)

    owner_ids = {tool.id for tool, _score in search_workflows("private voxel report", settings=settings, user_id="owner")}
    other_ids = {tool.id for tool, _score in search_workflows("private voxel report", settings=settings, user_id="other")}

    assert "private_voxel_report" in owner_ids
    assert "private_voxel_report" not in other_ids
    assert "private_voxel_report" in {
        tool["id"] for tool in run_analysis_workflows_payload(settings=settings, user_id="owner")
    }
    assert "private_voxel_report" not in {
        tool["id"] for tool in run_analysis_workflows_payload(settings=settings, user_id="other")
    }


def test_background_submission_captures_effective_workflow_definition(monkeypatch, tmp_path):
    captured: dict = {}
    workflow = resolve_workflow("mri_info").model_copy(
        update={"description": "Captured private workflow definition."}
    )

    def fake_submit(name, kwargs, *, queue, job_id):
        captured.update(name=name, kwargs=kwargs, queue=queue, job_id=job_id)
        return job_id

    monkeypatch.setattr(neuroimaging_tasks_module.job_manager, "submit", fake_submit)
    run = Run(id="run-1", case_id="case-1")

    result = neuroimaging_tasks_module.submit_neuroimaging_workflow(
        run=run,
        workflow=workflow,
        inputs=["/case/input.mgz"],
        bind_host_path=tmp_path,
        bind_container_path="/case",
        job_id="job-1",
        gpu_enabled=False,
    )

    assert result == "job-1"
    assert captured["queue"] == workflow.execution.queue
    assert captured["kwargs"]["workflow_definition"]["description"] == workflow.description
    assert captured["kwargs"]["tool_id"] == workflow.id


def test_prepare_images_selects_every_unique_catalog_image():
    assert prepare_images_module.workflow_images() == [
        "deepmi/fastsurfer:cu128-v2.5.4",
        "vnmd/freesurfer_8.1.0:20260311",
        "vnmd/fsqc_2.1.4:20251126",
    ]


def test_warm_gpu_capabilities_probes_each_gpu_workflow_image_once(monkeypatch):
    calls: list[tuple[bool, str | None]] = []

    def fake_resolve(preferred, *, image=None):
        calls.append((preferred, image))
        return True

    monkeypatch.setattr(execution_module, "resolve_gpu_enabled", fake_resolve)

    assert execution_module.warm_workflow_gpu_capabilities() == {
        "deepmi/fastsurfer:cu128-v2.5.4": True,
    }
    assert calls == [(True, "deepmi/fastsurfer:cu128-v2.5.4")]


def test_prepare_image_uses_persistent_arch_specific_sif(monkeypatch, tmp_path):
    monkeypatch.setenv("NEUROCADE_SIF_DIR", str(tmp_path))
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        Path(argv[-2]).write_bytes(b"sif")

    monkeypatch.setattr(prepare_images_module.subprocess, "run", fake_run)
    target = prepare_images_module.prepare_image("deepmi/fastsurfer:cu128-v2.5.4")

    assert target.is_file()
    assert target.name.startswith("deepmi_fastsurfer_cu128-v2.5.4-")
    assert calls[0][:4] == ["apptainer", "--quiet", "pull", "--force"]
    assert calls[0][-1] == "docker://deepmi/fastsurfer:cu128-v2.5.4"


def test_prepare_images_reports_required_cuda_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prepare-images"])
    monkeypatch.setattr(prepare_images_module, "workflow_images", lambda **_kwargs: ["vnmd/fastsurfer:tag"])
    monkeypatch.setattr(prepare_images_module, "prepare_image", lambda _image, **_kwargs: Path("tool.sif"))

    def unavailable(_preferred, **_kwargs):
        raise prepare_images_module.RuntimeGpuUnavailableError("tool image is CPU-only")

    monkeypatch.setattr(prepare_images_module, "resolve_gpu_enabled", unavailable)

    assert prepare_images_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: tool image is CPU-only\n"


def test_fsqc_has_fixed_fastsurfer_contract():
    workflow = resolve_workflow("fsqc")

    assert [item.name for item in workflow.inputs] == ["processed_orig"]
    assert [item.name for item in workflow.outputs] == ["metrics", "results_archive"]
    assert {item.type for item in workflow.outputs} == {"other"}
    assert "--fastsurfer" in workflow.script
    assert "--exit-on-error" in workflow.script
    assert "--no-group" not in workflow.script
    assert "${INPUTS[0]}" in workflow.script
    assert "${OUTPUTS[0]}" in workflow.script
    assert "${OUTPUTS[1]}" in workflow.script
    assert workflow.return_policy.include == ["return_code", "stdout", "stderr", "outputs"]


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (
            """
version: 1
tools:
  - id: unsafe
    image: freesurfer:latest
    description: unsafe
    details: unsafe
    script: echo unsafe
""",
            "explicit non-latest tag",
        ),
        (
            """
version: 1
tools:
  - id: unsafe
    image: freesurfer_8.1.0:20260311
    description: unsafe
    details: unsafe
    outputs:
      - name: escape
        type: other
        path: ../escape.mgz
        description: unsafe
    script: echo unsafe
""",
            "normalized relative path",
        ),
        (
            """
version: 1
tools:
  - id: unsafe
    image: freesurfer_8.1.0:20260311
    description: unsafe
    details: unsafe
    outputs:
      - name: root_file
        type: other
        path: .
        description: unsafe
    script: echo unsafe
""",
            "output path must name a file",
        ),
        (
            """
version: 1
tools:
  - id: unsafe
    image: freesurfer_8.1.0:20260311
    description: unsafe
    details: unsafe
    inputs: []
    script: echo "${INPUTS[0]}"
""",
            "undeclared input",
        ),
        (
            """
version: 1
tools:
  - id: unsafe
    image: freesurfer_8.1.0:20260311
    description: unsafe
    details: unsafe
    script: if broken; then
""",
            "invalid Bash",
        ),
        (
            """
version: 1
tools:
  - id: unsupported_runtime_variable
    image: freesurfer_8.1.0:20260311
    description: reject unavailable runtime variables
    details: reject unavailable runtime variables
    script: echo "${RUN_ID}"
""",
            "unsupported runtime variable.*RUN_ID",
        ),
        (
            """
version: 1
tools:
  - id: unquoted_runtime_path
    image: freesurfer_8.1.0:20260311
    description: reject unquoted paths
    details: reject unquoted paths
    inputs:
      - name: volume
        description: input
    script: mri_info ${INPUTS[0]}
""",
            "double-quote runtime path reference",
        ),
        (
            """
version: 1
tools:
  - id: ui_workflow
    image: freesurfer_8.1.0:20260311
    description: invalid UI contract
    details: invalid UI contract
    execution:
      mode: background
    ui:
      run_analysis: true
    script: echo invalid
""",
            "input_artifact_kind",
        ),
        (
            """
version: 1
tools:
  - id: typo
    image: freesurfer_8.1.0:20260311
    description: reject unknown fields
    details: reject unknown fields
    execution:
      gpuu: false
    script: echo typo
""",
            "Extra inputs are not permitted",
        ),
    ],
)
def test_catalog_rejects_unsafe_definitions(tmp_path, yaml_text, message):
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_workflow_catalog(path)


def test_prepare_workflow_validates_ordered_paths_and_symlink_escape(tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input volume.mgz").write_bytes(b"volume")
    outside = tmp_path / "outside.mgz"
    outside.write_bytes(b"outside")
    (case_root / "escape.mgz").symlink_to(outside)
    bind = RuntimeBind(case_root, "/case", "rw")

    prepared = execution_module.prepare_workflow(
        "mri_info_resolution",
        ["/case/input volume.mgz"],
        bind,
        run_id="run-1",
    )
    assert prepared.container_inputs == ("/case/input volume.mgz",)

    with pytest.raises(ValueError, match="escapes"):
        execution_module.prepare_workflow("mri_info", ["/case/escape.mgz"], bind)
    with pytest.raises(ValueError, match="exactly 1"):
        execution_module.prepare_workflow("mri_info", [], bind)


def test_typed_output_maps_to_declared_case_file_and_is_reported(tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")

    prepared = execution_module.prepare_workflow(
        "fastsurfer_full",
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        run_id="run-1",
    )

    output_path = case_root / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"segmentation")

    assert prepared.host_outputs[0] == output_path.resolve()
    assert prepared.container_outputs[0] == "/case/mri/aparc.DKTatlas+aseg.deep.mgz"
    output_record = execution_module._output_records(prepared)[0]
    assert output_record == {
        "name": "whole_brain_segmentation",
        "type": "segmentation_volume",
        "path": "/case/mri/aparc.DKTatlas+aseg.deep.mgz",
        "exists": True,
        "size_bytes": 12,
        "required": True,
        "state": "created",
    }
    script = execution_module.workflow_script(prepared)
    assert 'subjects_dir="$(dirname "${CASE_ROOT}")"' in script
    assert 'subject_id="$(basename "${CASE_ROOT}")"' in script
    assert "mkdir -p -- /case/label /case/mri /case/stats /case/surf" in script
    assert "cp " not in script


def test_fixed_command_is_quoted_and_streams_are_truncated(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    filename = "input $(touch should-not-run).mgz"
    (case_root / filename).write_bytes(b"volume")
    captured = {}

    def fake_execute(request):
        captured["request"] = request
        return RuntimeExecutionResult(
            request=request,
            returncode=0,
            stdout="x" * 10_000,
            stderr="",
        )

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    result = execution_module.execute_workflow(
        "mri_info_resolution",
        [f"/case/{filename}"],
        RuntimeBind(case_root, "/case", "rw"),
        run_id="run-1",
    )

    container_run = captured["request"].container_run
    assert container_run is not None
    script = container_run.command[-1]
    assert "mri_info --res" in script
    assert "'/case/input $(touch should-not-run).mgz'" in script
    assert "[truncated" in result["stdout"]
    assert len(result["stdout"]) <= resolve_workflow("mri_info_resolution").return_policy.max_stream_chars
    assert not (case_root / ".runs" / "run-1").exists()


def test_failed_workflow_returns_code_and_stderr(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")

    def fake_execute(request):
        return RuntimeExecutionResult(request=request, returncode=7, stdout="", stderr="invalid header")

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    result = execution_module.execute_workflow(
        "mri_info",
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
    )

    assert result["status"] == "failed"
    assert result["return_code"] == 7
    assert result["stderr"] == "invalid header"


def test_failed_direct_root_workflow_retains_partial_outputs(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")

    def fake_execute(request):
        partial = case_root / "mri" / "orig.mgz"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        return RuntimeExecutionResult(request=request, returncode=7, stdout="", stderr="segmentation failed")

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    result = execution_module.execute_workflow(
        "fastsurfer_full",
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        run_id="failed-run",
        gpu_enabled=False,
    )

    assert result["status"] == "failed"
    assert (case_root / "mri" / "orig.mgz").read_bytes() == b"partial"
    states = {output["name"]: output["state"] for output in result["outputs"]}
    assert states["conformed_input"] == "created"
    assert states["whole_brain_segmentation"] == "missing"
    assert not (case_root / ".runs" / "failed-run").exists()


def test_workflow_reports_unchanged_existing_output_as_preexisting(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    output_path = case_root / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
    output_path.parent.mkdir(parents=True)
    (case_root / "input.mgz").write_bytes(b"volume")
    output_path.write_bytes(b"old segmentation")

    def fake_execute(request):
        return RuntimeExecutionResult(request=request, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    result = execution_module.execute_workflow(
        "fastsurfer_full",
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        run_id="rerun",
        gpu_enabled=False,
    )

    output = next(item for item in result["outputs"] if item["name"] == "whole_brain_segmentation")
    assert output["state"] == "preexisting"
