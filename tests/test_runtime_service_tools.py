"""Test runtime service tools behavior for NeuroCade."""

import asyncio
from pathlib import Path
import sys
from typing import cast

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime import service as runtime_module  # noqa: E402
from api_service.runtime import fastsurfer_tasks as fastsurfer_tasks_module  # noqa: E402
from api_service.runtime.service import RuntimeService  # noqa: E402
from api_service.runtime_tools import handlers as handler_module  # noqa: E402
from api_service.runtime_tools import container_commands as container_commands_module  # noqa: E402
from api_service.runtime_tools import read_stats as read_stats_module  # noqa: E402
from api_service.runtime_tools.types import ToolTextContent  # noqa: E402
from neurocade_runtime_tools.execution import (  # noqa: E402
    RuntimeContainerRunRequest,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeWorkspaceArtifactSyncTarget,
)


@pytest.fixture()
def runtime_case(tmp_path, monkeypatch):
    """Create a runtime service pointed at a temporary FastSurfer case."""
    data_root = tmp_path / "neurocade-data"
    output_dir = data_root / "output"
    workspace_id = "workspace-1"
    case_slug = "case-a"
    case_id = f"{workspace_id}__{case_slug}"
    case_output = Path("workspaces") / workspace_id / "cases" / case_slug
    case_dir = output_dir / case_output
    (case_dir / "mri").mkdir(parents=True)
    (case_dir / "stats").mkdir()
    (case_dir / "input.mgz").write_bytes(b"input")
    (case_dir / "mri" / "orig.mgz").write_bytes(b"volume")
    (case_dir / "stats" / "aseg+DKT.stats").write_text(
        "\n".join(
            [
                "# Measure BrainSeg, BrainSegVol, Brain Segmentation Volume, 123.0, mm^3",
                "1 17 42 456.7 Left-Hippocampus 0 0 0",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(runtime_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(runtime_module.settings, "outputs_dir_override", output_dir)
    monkeypatch.setattr(container_commands_module, "HOST_DATA_DIR", str(data_root))
    monkeypatch.setattr(container_commands_module, "LOCAL_DATA_ROOT", str(data_root))
    monkeypatch.setattr(container_commands_module, "LOCAL_OUTPUT_ROOT", str(output_dir))
    monkeypatch.setattr(handler_module, "HOST_DATA_DIR", str(data_root), raising=False)
    monkeypatch.setattr(handler_module, "LOCAL_OUTPUT_ROOT", str(output_dir), raising=False)
    monkeypatch.setattr(read_stats_module, "_OUTPUT_DIR", str(output_dir))

    state = {
        "current_workspace_id": workspace_id,
        "current_case_id": case_id,
        "current_intensity_artifact_id": "artifact-input",
        "current_intensity_volume": "input.mgz",
        "loaded_volumes": ["orig.mgz"],
        "has_valid_segmentation": True,
        "is_job_running": False,
    }
    return RuntimeService(), state


def test_gui_run_fastsurfer_without_selected_input_requests_frontend_selector(runtime_case):
    service, state = runtime_case
    state = {
        **state,
        "current_intensity_artifact_id": None,
        "current_intensity_volume": None,
    }

    result = asyncio.run(
        service.call_tool(
            "gui_run_fastsurfer",
            {"case_name": "case-a", "seg_only": True},
            gui_state_override=state,
        )
    )

    gui_state = service.gui_state_for_key()
    requested_run = gui_state["requested_run_fastsurfer"]
    assert "will ask the user to choose an input volume" in result
    assert requested_run == {
        "case_id": "workspace-1__case-a",
        "seg_only": True,
        "case_name": "case-a",
    }
    assert gui_state["is_job_running"] is False


def test_start_run_submits_fastsurfer_runtime_request(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    data_root = tmp_path / "neurocade-data"
    output_dir = data_root / "output"

    monkeypatch.setattr(runtime_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(runtime_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(runtime_module.settings, "outputs_dir_override", output_dir)

    def fake_submit_runtime_request(task, request, *, kwargs=None):
        captured["task"] = task
        captured["request"] = request
        captured["kwargs"] = kwargs
        request.task_id = "task-fastsurfer-1"
        return RuntimeExecutionResult(request=request, returncode=0, submitted_task_id="task-fastsurfer-1")

    monkeypatch.setattr(runtime_module, "submit_runtime_request", fake_submit_runtime_request)

    result = asyncio.run(
        RuntimeService().start_run(
            {
                "case_id": "workspace-1__case-a",
                "workspace_id": "workspace-1",
                "user_id": "user-1",
                "subject_name": "case-a",
                "input_path": "/data/input.mgz",
                "seg_only": True,
            }
        )
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    kwargs = cast(dict[str, object], captured["kwargs"])
    assert result == {"case_id": "workspace-1__case-a", "task_id": "task-fastsurfer-1", "status": "queued"}
    assert request.synchronous is False
    assert request.execution_mode == "job-submit"
    assert request.queue_name == runtime_module.FASTSURFER_QUEUE
    assert request.user_id == "user-1"
    assert request.workspace_id == "workspace-1"
    assert request.case_id == "workspace-1__case-a"
    assert request.artifact_index_targets
    assert request.artifact_index_targets[0].case_title == "case-a"
    assert kwargs["case_id"] == "workspace-1__case-a"
    assert kwargs["workspace_id"] == "workspace-1"
    assert kwargs["output_case_dir_name"] == "case-a"
    assert kwargs["seg_only"] is True


def test_start_run_uses_local_case_slug_for_storage(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    data_root = tmp_path / "neurocade-data"
    output_dir = data_root / "output"

    monkeypatch.setattr(runtime_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(runtime_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(runtime_module.settings, "outputs_dir_override", output_dir)

    def fake_submit_runtime_request(task, request, *, kwargs=None):
        captured["request"] = request
        captured["kwargs"] = kwargs
        request.task_id = "task-fastsurfer-1"
        return RuntimeExecutionResult(request=request, returncode=0, submitted_task_id="task-fastsurfer-1")

    monkeypatch.setattr(runtime_module, "submit_runtime_request", fake_submit_runtime_request)

    result = asyncio.run(
        RuntimeService().start_run(
            {
                "case_id": "workspace-1__case-a",
                "workspace_id": "workspace-1",
                "user_id": "user-1",
                "subject_name": "case-a",
                "input_path": "/data/input.mgz",
                "seg_only": True,
            }
        )
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    kwargs = cast(dict[str, object], captured["kwargs"])
    case_dir = output_dir / "workspaces" / "workspace-1" / "cases" / "case-a"
    assert result == {"case_id": "workspace-1__case-a", "task_id": "task-fastsurfer-1", "status": "queued"}
    assert request.case_id == "workspace-1__case-a"
    assert request.output_root == case_dir.parent
    assert kwargs["case_id"] == "workspace-1__case-a"
    assert kwargs["output_case_dir_name"] == "case-a"
    assert (case_dir / "status.json").exists()


def test_fastsurfer_worker_task_uses_fastsurfer_queue(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    output_dir = tmp_path / "output"

    monkeypatch.setattr(fastsurfer_tasks_module, "resolve_fastsurfer_device", lambda: "cpu")

    def fake_execute_runtime_request(request, *, run_completion_hooks=True):
        captured["request"] = request
        captured["run_completion_hooks"] = run_completion_hooks
        return RuntimeExecutionResult(request=request, returncode=0)

    monkeypatch.setattr(fastsurfer_tasks_module, "execute_runtime_request", fake_execute_runtime_request)

    result = fastsurfer_tasks_module.run_fastsurfer_task(
        case_id="workspace-1__case-a",
        workspace_id="workspace-1",
        input_path="/data/input.mgz",
        output_dir=str(output_dir),
        seg_only=True,
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    assert result["status"] == "completed"
    assert request.queue_name == fastsurfer_tasks_module.FASTSURFER_QUEUE
    assert request.queue_name == runtime_module.FASTSURFER_QUEUE
    assert request.execution_mode == "container"
    assert request.container_run is not None
    assert request.container_run.image
    assert captured["run_completion_hooks"] is False


def test_fastsurfer_worker_writes_to_local_case_slug(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    output_dir = tmp_path / "output"

    monkeypatch.setattr(fastsurfer_tasks_module, "resolve_fastsurfer_device", lambda: "cpu")

    def fake_execute_runtime_request(request, *, run_completion_hooks=True):
        captured["request"] = request
        return RuntimeExecutionResult(request=request, returncode=0)

    monkeypatch.setattr(fastsurfer_tasks_module, "execute_runtime_request", fake_execute_runtime_request)

    result = fastsurfer_tasks_module.run_fastsurfer_task(
        case_id="workspace-1__case-a",
        workspace_id="workspace-1",
        input_path="/data/input.mgz",
        output_dir=str(output_dir),
        output_case_dir_name="case-a",
        seg_only=True,
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    assert result["status"] == "completed"
    assert result["output_path"] == f"{output_dir}/case-a"
    assert request.case_id == "workspace-1__case-a"
    assert (output_dir / "case-a" / "status.json").exists()
    assert not (output_dir / "workspace-1__case-a").exists()


def test_workspace_case_bash_does_not_mount_freesurfer_license(monkeypatch, tmp_path):
    data_root = tmp_path / "neurocade-data"
    case_dir = data_root / "output" / "workspaces" / "workspace-1" / "cases" / "case-a"
    case_dir.mkdir(parents=True)
    (data_root / "license.txt").write_text("ignored", encoding="utf-8")

    monkeypatch.setattr(container_commands_module, "HOST_DATA_DIR", str(data_root))
    monkeypatch.setattr(container_commands_module, "ROOT_DIR", tmp_path)

    cmd = container_commands_module._docker_run_workspace_case_bash(
        "echo ok",
        case_dir=str(case_dir),
    )
    assert all(bind.container_path != "/fs_license.txt" for bind in cmd.binds)
    assert "FS_LICENSE" not in (cmd.env or {})
    assert cmd.command[-1] == "echo ok"


def test_fastsurfer_worker_uses_container_default_license(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    data_root = tmp_path / "neurocade-data"
    output_dir = data_root / "output"
    data_root.mkdir()
    (data_root / "license.txt").write_text("ignored", encoding="utf-8")

    monkeypatch.setattr(fastsurfer_tasks_module, "HOST_DATA_DIR", str(data_root))
    monkeypatch.setattr(fastsurfer_tasks_module, "resolve_fastsurfer_device", lambda: "cpu")

    def fake_execute_runtime_request(request, *, run_completion_hooks=True):
        captured["request"] = request
        return RuntimeExecutionResult(request=request, returncode=0)

    monkeypatch.setattr(fastsurfer_tasks_module, "execute_runtime_request", fake_execute_runtime_request)

    result = fastsurfer_tasks_module.run_fastsurfer_task(
        case_id="workspace-1__case-a",
        workspace_id="workspace-1",
        input_path="/data/input.mgz",
        output_dir=str(output_dir),
        seg_only=True,
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    assert result["status"] == "completed"
    assert request.container_run is not None
    assert all(bind.container_path != "/fs_license.txt" for bind in request.container_run.binds)
    assert "FS_LICENSE" not in (request.container_run.env or {})
    assert "--fs_license" not in request.container_run.command


@pytest.mark.parametrize(("seg_only", "surf_only"), [(False, False), (False, True), (True, False)])
def test_fastsurfer_worker_runs_without_license_file(monkeypatch, tmp_path, seg_only, surf_only):
    captured: dict[str, object] = {}
    data_root = tmp_path / "neurocade-data"
    output_dir = data_root / "output"
    data_root.mkdir()

    monkeypatch.setattr(fastsurfer_tasks_module, "HOST_DATA_DIR", str(data_root))
    monkeypatch.setattr(fastsurfer_tasks_module, "resolve_fastsurfer_device", lambda: "cpu")

    def fake_execute_runtime_request(request, *, run_completion_hooks=True):
        captured["request"] = request
        return RuntimeExecutionResult(request=request, returncode=0)

    monkeypatch.setattr(fastsurfer_tasks_module, "execute_runtime_request", fake_execute_runtime_request)

    result = fastsurfer_tasks_module.run_fastsurfer_task(
        case_id="workspace-1__case-a",
        workspace_id="workspace-1",
        input_path="/data/input.mgz",
        output_dir=str(output_dir),
        seg_only=seg_only,
        surf_only=surf_only,
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    assert result["status"] == "completed"
    assert request.container_run is not None
    assert all(bind.container_path != "/fs_license.txt" for bind in request.container_run.binds)
    assert "FS_LICENSE" not in (request.container_run.env or {})
    assert "--fs_license" not in request.container_run.command


def test_runtime_service_exposes_expected_llm_tools(runtime_case):
    service, state = runtime_case
    tools = asyncio.run(service.fetch_tools(gui_state_override=state))
    names = {tool["function"]["name"] for tool in tools}

    assert {
        "freesurfer_lut",
        "case_file_tree",
        "read_stats",
        "gui_run_fastsurfer",
        "gui_review_segmentation",
        "gui_load_volume",
        "gui_close_volume",
        "gui_select_volume",
        "gui_adjust_display",
        "gui_move_cursor",
        "gui_focus_label",
    }.issubset(names)
    lut_tool = next(tool for tool in tools if tool["function"]["name"] == "freesurfer_lut")
    assert "volume_path" in lut_tool["function"]["parameters"]["properties"]


def test_runtime_service_exposes_case_tools_without_gui_context():
    service = RuntimeService()
    tools = asyncio.run(service.fetch_tools(gui_state_override={}))
    names = {tool["function"]["name"] for tool in tools}

    assert "gui_load_volume" in names
    assert "gui_close_volume" in names
    assert "gui_move_cursor" in names
    assert "gui_review_segmentation" in names
    assert "read_stats" in names

    result = asyncio.run(service.call_tool("gui_move_cursor", {"x": 1, "y": 2, "z": 3}, gui_state_override={}))
    assert "requires at least one loaded volume" in result


def test_workspace_command_passes_runtime_metadata(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    sync_target = RuntimeWorkspaceArtifactSyncTarget(
        run_id="workflow-1",
        analysis_dir=tmp_path / "analysis",
    )
    db = object()

    def fake_execute_workspace_bash(arguments):
        captured["arguments"] = arguments
        return RuntimeContainerRunRequest(image="neurocade-runtime-bash:test", command=["/bin/bash", "-lc", "echo ok"])

    def fake_run_synchronous_runtime_task(name, cmd, **kwargs):
        captured["task_name"] = name
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return [ToolTextContent(type="text", text="ok")]

    monkeypatch.setattr(runtime_module, "execute_workspace_bash", fake_execute_workspace_bash)
    monkeypatch.setattr(runtime_module, "run_synchronous_runtime_task", fake_run_synchronous_runtime_task)

    result = asyncio.run(
        RuntimeService().run_workspace_command(
            command="find /cases -type f > /workspace/files.txt",
            cases_dir=str(tmp_path / ".workspace-inputs" / "workflow-1" / "cases"),
            workspace_dir=str(tmp_path / "output" / "workspaces" / "workspace-1" / "workspace-analyses" / "workflow-1"),
            db=db,
            workspace_artifact_sync_targets=(sync_target,),
            queue_name="workspace_batch",
            task_id="task-workspace-1",
        )
    )

    assert result == "ok"
    assert captured["arguments"] == {
        "command": "find /cases -type f > /workspace/files.txt",
        "cases_dir": str(tmp_path / ".workspace-inputs" / "workflow-1" / "cases"),
        "workspace_dir": str(tmp_path / "output" / "workspaces" / "workspace-1" / "workspace-analyses" / "workflow-1"),
    }
    assert captured["task_name"] == "workspace_bash"
    assert captured["kwargs"] == {
        "db": db,
        "workspace_artifact_sync_targets": (sync_target,),
        "queue_name": "workspace_batch",
        "task_id": "task-workspace-1",
    }


def test_case_dir_resolution_requires_workspace_id(monkeypatch, tmp_path):
    """A duplicate case id in another workspace must not be discovered by scanning."""
    data_root = tmp_path
    output_dir = data_root / "output"
    wrong_case_dir = output_dir / "workspaces" / "workspace-b" / "cases" / "case-1"
    wrong_case_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(runtime_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(runtime_module.settings, "outputs_dir_override", output_dir)

    assert runtime_module._case_dir_for_id("workspace-a__case-1", "") is None
    assert runtime_module._case_dir_for_id("workspace-a__case-1", "workspace-a") == output_dir / "workspaces" / "workspace-a" / "cases" / "case-1"
    assert runtime_module._case_dir_for_id("workspace-a__case-1", "workspace-a") != wrong_case_dir


def test_synchronous_runtime_task_threads_workspace_sync_metadata(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    db = object()
    sync_target = RuntimeWorkspaceArtifactSyncTarget(
        run_id="workflow-1",
        analysis_dir=tmp_path / "analysis",
    )

    def fake_execute_runtime_request(request, *, db=None):
        captured["request"] = request
        captured["db"] = db
        return RuntimeExecutionResult(request=request, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(container_commands_module, "execute_runtime_request", fake_execute_runtime_request)

    result = container_commands_module.run_synchronous_runtime_task(
        "workspace_bash",
        RuntimeContainerRunRequest(image="neurocade-runtime-bash:test", command=["/bin/bash", "-lc", "echo ok"]),
        db=db,
        workspace_artifact_sync_targets=(sync_target,),
        queue_name="workspace_batch",
        task_id="task-workspace-1",
    )

    request = cast(RuntimeExecutionRequest, captured["request"])
    assert result[0].text.startswith("Successfully executed workspace_bash.")
    assert captured["db"] is db
    assert request.queue_name == "workspace_batch"
    assert request.task_id == "task-workspace-1"
    assert request.workspace_artifact_sync_targets == (sync_target,)


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("freesurfer_lut", {"query": "17"}, "Left-Hippocampus"),
        ("case_file_tree", {}, "/case/"),
        ("read_stats", {"label_query": "Left-Hippocampus"}, "456.700"),
        ("gui_run_fastsurfer", {"case_name": "case-a", "seg_only": True}, "Successfully triggered FastSurfer"),
        ("gui_review_segmentation", {}, "REVIEW_SEGMENTATION"),
        ("gui_load_volume", {"file_path": "/case/mri/orig.mgz"}, "LOAD_VOLUME"),
        ("gui_close_volume", {"volume_id": "orig.mgz"}, "CLOSE_VOLUME"),
        ("gui_select_volume", {"intensity_volume": "orig.mgz", "segmentation_volume": ""}, "SELECT_VOLUMES"),
        ("gui_adjust_display", {"opacity": 0.5}, "ADJUST_DISPLAY"),
        ("gui_move_cursor", {"x": 1, "y": 2, "z": 3}, "MOVE_CURSOR"),
        ("gui_focus_label", {"label_query": "Left-Hippocampus"}, "Failed to focus label"),
    ],
)
def test_runtime_service_executes_each_case_tool(runtime_case, name, arguments, expected):
    service, state = runtime_case
    result = asyncio.run(service.call_tool(name, arguments, gui_state_override=state))
    assert expected in result


def test_gui_tool_override_mutations_are_visible_to_sync(runtime_case):
    service, state = runtime_case
    state_key = "gui-regression"

    result = asyncio.run(
        service.call_tool(
            "gui_close_volume",
            {"volume_id": "orig.mgz"},
            gui_state_override={**state, "current_cursor": {"voxel": [1, 2, 3]}},
            gui_state_key=state_key,
        )
    )
    response = asyncio.run(service.sync_gui_state(state, gui_state_key=state_key))

    assert "CLOSE_VOLUME" in result
    assert response["requested_close_volumes"] == [{"volume_id": "orig.mgz"}]


def test_freesurfer_lut_filters_results_to_labels_present_in_volume(runtime_case):
    from typing import Any, cast

    import nibabel as nib
    import numpy as np

    service, state = runtime_case
    case_slug = str(state["current_case_id"]).removeprefix(f"{state['current_workspace_id']}__")
    case_dir = Path(container_commands_module.LOCAL_OUTPUT_ROOT) / "workspaces" / state["current_workspace_id"] / "cases" / case_slug
    volume_path = case_dir / "mri" / "labels.nii.gz"
    data = np.zeros((3, 3, 3), dtype=np.int16)
    data[0, 0, 0] = 17
    data[1, 1, 1] = 251
    nib_module = cast(Any, nib)
    nib_module.save(nib_module.Nifti1Image(data, affine=np.eye(4)), volume_path)

    result = asyncio.run(
        service.call_tool(
            "freesurfer_lut",
            {"query": "corpus callosum", "volume_path": "/case/mri/labels.nii.gz"},
            gui_state_override=state,
        )
    )

    assert "Filtered to 3 unique integer label ID(s)" in result
    assert "251\tCC_Posterior" in result
    assert "192\tCorpus_Callosum" not in result
    assert "1004\tctx-lh-corpuscallosum" not in result
