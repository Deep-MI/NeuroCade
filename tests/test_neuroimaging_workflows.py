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
from api_service.runtime_tools import workflow_execution as execution_module  # noqa: E402
from api_service.runtime_tools.workflow_catalog import (  # noqa: E402
    NeuroimagingWorkflow,
    WorkflowExecution,
    WorkflowReturn,
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
from neurocade_runtime_tools.images import load_image_manifest  # noqa: E402

from backend_common.db import Run  # noqa: E402


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    load_workflow_catalog.cache_clear()
    yield
    load_workflow_catalog.cache_clear()


def header_probe_workflow():
    """Build a small private workflow used to test generic execution behavior."""
    return resolve_workflow("fastsurfer_fast").model_copy(
        update={
            "id": "header_probe",
            "outputs": [],
            "script": 'probe_volume --resolution "${INPUTS[0]}"',
            "execution": WorkflowExecution(gpu=False),
            "return_policy": WorkflowReturn(max_stream_chars=4096),
        }
    )
def test_default_catalog_has_fixed_workflows_and_defaults():
    catalog = load_workflow_catalog()
    tools = {tool.id: tool for tool in catalog.tools}
    defaults = WorkflowExecution()

    assert set(tools) == {"fastsurfer_full", "fastsurfer_segmentation", "fastsurfer_fast"}
    assert tools["fastsurfer_full"].neurodesk_image == "vnmd/fastsurfer_2.4.2:20260115"
    assert tools["fastsurfer_full"].execution.gpu is False
    assert tools["fastsurfer_full"].execution.mode == "background"
    for tool_id in ("fastsurfer_full", "fastsurfer_segmentation", "fastsurfer_fast"):
        assert all(output.path != "." for output in tools[tool_id].outputs)
        assert all(output.type in {"intensity_volume", "segmentation_volume", "surface", "other"} for output in tools[tool_id].outputs)
        assert tools[tool_id].case_output_folder == "fastsurfer_output"
        assert '--sd "${OUTPUT_PARENT}"' in tools[tool_id].script
        assert '--sid "${OUTPUT_NAME}"' in tools[tool_id].script
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


def test_search_payload_is_compact_and_inspect_is_lazy():
    payload = run_analysis_workflows_payload()
    assert [tool["id"] for tool in payload] == [
        "fastsurfer_full",
        "fastsurfer_segmentation",
        "fastsurfer_fast",
    ]
    assert {tool["input_artifact_kind"] for tool in payload} == {"intensity_volume"}
    inspected = inspect_workflow("fastsurfer_full")
    assert "details" in inspected
    assert inspected["image"] == "vnmd/fastsurfer_2.4.2:20260115"
    assert inspected["inputs"][0]["name"] == "t1"
    assert inspected["outputs"][0]["path"] == "fastsurfer_output/mri/aparc.DKTatlas+aseg.deep.mgz"
    assert "script" not in inspected


def test_user_workflow_overlays_are_isolated_and_reload_immediately(tmp_path):
    settings = SimpleNamespace(outputs_dir=tmp_path / "output")
    base = resolve_workflow("fastsurfer_fast").model_dump(mode="json", by_alias=True, exclude_none=True)
    first = {**base, "description": "First user's private MRI information workflow."}
    second = {**base, "description": "Second user's private MRI information workflow."}

    upsert_user_workflow(settings, "user/one", first)
    upsert_user_workflow(settings, "user:two", second)

    assert resolve_workflow("fastsurfer_fast").description == base["description"]
    assert resolve_workflow("fastsurfer_fast", settings=settings, user_id="user/one").description == first["description"]
    assert resolve_workflow("fastsurfer_fast", settings=settings, user_id="user:two").description == second["description"]
    assert workflow_source("fastsurfer_fast", settings=settings, user_id="user/one") == "user_override"
    assert user_workflow_catalog_path(settings, "user/one").parent == settings.outputs_dir / ".user-tool-configs"
    assert "user/one" not in str(user_workflow_catalog_path(settings, "user/one"))

    delete_user_workflow(settings, "user/one", "fastsurfer_fast")

    assert resolve_workflow("fastsurfer_fast", settings=settings, user_id="user/one").description == base["description"]
    assert workflow_source("fastsurfer_fast", settings=settings, user_id="user/one") == "built_in"
    assert resolve_workflow("fastsurfer_fast", settings=settings, user_id="user:two").description == second["description"]


def test_user_workflow_upsert_rejects_invalid_definition_without_changing_overlay(tmp_path):
    settings = SimpleNamespace(outputs_dir=tmp_path / "output")
    definition = resolve_workflow("fastsurfer_fast").model_dump(mode="json", by_alias=True, exclude_none=True)
    definition["description"] = "Valid private definition."
    upsert_user_workflow(settings, "user-1", definition)
    path = user_workflow_catalog_path(settings, "user-1")
    original = path.read_bytes()

    invalid = {**definition, "image": "freesurfer:latest"}
    with pytest.raises(ValueError, match="explicit non-latest tag"):
        upsert_user_workflow(settings, "user-1", invalid)

    assert path.read_bytes() == original
    assert resolve_workflow("fastsurfer_fast", settings=settings, user_id="user-1").description == definition["description"]


def test_user_created_workflow_appears_only_in_its_owner_search(tmp_path):
    settings = SimpleNamespace(outputs_dir=tmp_path / "output")
    definition = resolve_workflow("fastsurfer_fast").model_dump(mode="json", by_alias=True, exclude_none=True)
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
    workflow = resolve_workflow("fastsurfer_fast").model_copy(
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


def test_default_image_manifest_contains_workflows_and_dicom_conversion():
    manifest = load_image_manifest(ROOT / "config" / "tool_images.json")
    assert [spec.image for spec in manifest] == [
        "vnmd/fastsurfer_2.4.2:20260115",
        "vnmd/dcm2niix_v1.0.20240202:20260512",
    ]
    assert all(spec.sif_url and spec.sif_sha256 for spec in manifest)


def test_warm_gpu_capabilities_probes_each_gpu_workflow_image_once(monkeypatch):
    calls: list[tuple[bool, str | None]] = []

    def fake_resolve(preferred, *, image=None):
        calls.append((preferred, image))
        return True

    monkeypatch.setattr(execution_module, "resolve_gpu_enabled", fake_resolve)

    assert execution_module.warm_workflow_gpu_capabilities() == {}
    assert calls == []


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
    script: probe_volume ${INPUTS[0]}
""",
            "double-quote runtime path reference",
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
    workflow = header_probe_workflow()

    prepared = execution_module.prepare_workflow(
        workflow.id,
        ["/case/input volume.mgz"],
        bind,
        workflow=workflow,
        run_id="run-1",
    )
    assert prepared.container_inputs == ("/case/input volume.mgz",)

    with pytest.raises(ValueError, match="escapes"):
        execution_module.prepare_workflow(workflow.id, ["/case/escape.mgz"], bind, workflow=workflow)
    with pytest.raises(ValueError, match="exactly 1"):
        execution_module.prepare_workflow(workflow.id, [], bind, workflow=workflow)


def test_typed_output_maps_to_declared_case_file_and_is_reported(tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")

    prepared = execution_module.prepare_workflow(
        "fastsurfer_full",
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        run_id="run-1",
        gpu_enabled=False,
    )

    output_path = case_root / "fastsurfer_output" / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"segmentation")

    assert prepared.host_outputs[0] == output_path.resolve()
    assert prepared.container_outputs[0] == "/case/fastsurfer_output/mri/aparc.DKTatlas+aseg.deep.mgz"
    assert prepared.runtime_outputs[0] == "/workflow_output/output/mri/aparc.DKTatlas+aseg.deep.mgz"
    output_record = execution_module._output_records(prepared)[0]
    assert output_record == {
        "name": "whole_brain_segmentation",
        "type": "segmentation_volume",
        "path": "/case/fastsurfer_output/mri/aparc.DKTatlas+aseg.deep.mgz",
        "exists": True,
        "size_bytes": 12,
        "required": True,
        "state": "created",
    }
    script = execution_module.workflow_script(prepared)
    assert "readonly OUTPUT_PARENT=/workflow_output" in script
    assert "readonly OUTPUT_NAME=output" in script
    assert "readonly OUTPUT_ROOT=/workflow_output/output" in script
    assert "mkdir -p -- /workflow_output/output/label /workflow_output/output/mri" in script
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
    workflow = header_probe_workflow()
    result = execution_module.execute_workflow(
        workflow.id,
        [f"/case/{filename}"],
        RuntimeBind(case_root, "/case", "rw"),
        workflow=workflow,
        run_id="run-1",
    )

    container_run = captured["request"].container_run
    assert container_run is not None
    script = container_run.command[-1]
    assert "probe_volume --resolution" in script
    assert "'/case/input $(touch should-not-run).mgz'" in script
    assert "[truncated" in result["stdout"]
    assert len(result["stdout"]) <= workflow.return_policy.max_stream_chars
    assert not (case_root / ".runs" / "run-1").exists()


def test_case_output_folder_mount_plan_is_image_agnostic(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")
    captured = {}

    def fake_execute(request):
        captured["request"] = request
        return RuntimeExecutionResult(request=request, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    workflow = resolve_workflow("fastsurfer_fast").model_copy(
        update={
            "id": "generic_output_tool",
            "image": "example/tool:1.0",
            "case_output_folder": "generic_results",
            "script": 'generic_tool --input "${INPUTS[0]}" --output "${OUTPUTS[0]}"',
        }
    )
    execution_module.execute_workflow(
        workflow.id,
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        workflow=workflow,
        run_id="run-1",
        gpu_enabled=False,
    )

    run = captured["request"].container_run
    assert run is not None
    assert run.scratch_paths == ("/workflow_output",)
    assert [(Path(bind.host_path), bind.container_path, bind.mode) for bind in run.binds] == [
        (case_root.resolve(), "/case", "rw"),
        ((case_root / "generic_results").resolve(), "/workflow_output/output", "rw"),
    ]
    assert (case_root / "generic_results").is_dir()
    script = run.command[-1]
    assert "readonly OUTPUT_PARENT=/workflow_output" in script
    assert "readonly OUTPUT_NAME=output" in script
    assert "readonly OUTPUT_ROOT=/workflow_output/output" in script
    assert "dirname" not in script and "basename" not in script


@pytest.mark.parametrize("value", ["../escape", "/absolute", ".runs", "folder/{run_id}"])
def test_case_output_folder_rejects_unsafe_paths(value):
    payload = resolve_workflow("fastsurfer_fast").model_dump(mode="json", by_alias=True)
    payload["case_output_folder"] = value
    with pytest.raises(ValueError, match="case_output_folder"):
        NeuroimagingWorkflow.model_validate(payload)


def test_failed_workflow_returns_code_and_stderr(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")

    def fake_execute(request):
        return RuntimeExecutionResult(request=request, returncode=7, stdout="", stderr="invalid header")

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    workflow = header_probe_workflow()
    result = execution_module.execute_workflow(
        workflow.id,
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        workflow=workflow,
    )

    assert result["status"] == "failed"
    assert result["return_code"] == 7
    assert result["stderr"] == "invalid header"


def test_failed_workflow_preserves_preparation_error_with_empty_log_file(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")
    stdout_path = case_root / "stdout.log"
    stderr_path = case_root / "stderr.log"
    stdout_path.touch()
    stderr_path.touch()

    def fake_execute(request):
        return RuntimeExecutionResult(
            request=request,
            returncode=1,
            stdout="",
            stderr='Docker image pull failed: exec: "docker-credential-desktop": executable file not found',
        )

    monkeypatch.setattr(execution_module, "execute_runtime_request", fake_execute)
    workflow = header_probe_workflow()
    result = execution_module.execute_workflow(
        workflow.id,
        ["/case/input.mgz"],
        RuntimeBind(case_root, "/case", "rw"),
        workflow=workflow,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert result["status"] == "failed"
    assert "docker-credential-desktop" in result["stderr"]


def test_failed_direct_root_workflow_retains_partial_outputs(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "input.mgz").write_bytes(b"volume")

    def fake_execute(request):
        partial = case_root / "fastsurfer_output" / "mri" / "orig.mgz"
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
    assert (case_root / "fastsurfer_output" / "mri" / "orig.mgz").read_bytes() == b"partial"
    states = {output["name"]: output["state"] for output in result["outputs"]}
    assert states["conformed_input"] == "created"
    assert states["whole_brain_segmentation"] == "missing"
    assert not (case_root / ".runs" / "failed-run").exists()


def test_workflow_reports_unchanged_existing_output_as_preexisting(monkeypatch, tmp_path):
    case_root = tmp_path / "case"
    output_path = case_root / "fastsurfer_output" / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
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
