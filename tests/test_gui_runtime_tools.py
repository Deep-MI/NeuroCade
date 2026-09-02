"""Test GUI runtime tool behavior for NeuroCade."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime import settings as runtime_settings  # noqa: E402
from api_service.runtime.gui_runtime import GuiRuntime  # noqa: E402
from api_service.runtime_tools import viewer_paths as viewer_paths_module  # noqa: E402


@pytest.fixture()
def runtime_case(tmp_path, monkeypatch):
    """Create a GUI runtime pointed at a temporary FastSurfer case."""
    data_root = tmp_path / "neurocade-data"
    output_dir = data_root / "output"
    workspace_id = "workspace-1"
    case_slug = "case-a"
    case_id = "case-id"
    case_output = Path("workspaces") / workspace_id / "cases" / case_slug
    case_dir = output_dir / case_output
    (case_dir / "mri").mkdir(parents=True)
    (output_dir / "workspaces" / workspace_id / ".neurocade-workspace.json").write_text(
        json.dumps({"id": workspace_id}), encoding="utf-8"
    )
    (case_dir / ".neurocade-case.json").write_text(json.dumps({"id": case_id}), encoding="utf-8")
    (case_dir / "stats").mkdir()
    (case_dir / "input.mgz").write_bytes(b"input")
    (case_dir / "mri" / "orig.mgz").write_bytes(b"volume")
    (case_dir / "stats" / "aseg+DKT.VINN.stats").write_text(
        "\n".join(
            [
                "# Measure BrainSeg, BrainSegVol, Brain Segmentation Volume, 123.0, mm^3",
                "1 17 42 456.7 Left-Hippocampus 0 0 0",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime_settings, "fs_data_root", data_root)

    state = {
        "workspace_id": workspace_id,
        "case_id": case_id,
        "current_intensity_artifact_id": "artifact-input",
        "current_intensity_volume": "input.mgz",
        "layers": [
            {
                "id": "orig.mgz",
                "filename": "orig.mgz",
                "name": "orig",
                "type": "intensity",
                "role": "intensity",
                "visible": True,
                "opacity": 1.0,
            },
            {
                "id": "lh.pial",
                "filename": "lh.pial",
                "name": "Left pial surface",
                "type": "surface",
                "role": "pial",
                "hemisphere": "left",
                "visible": False,
                "opacity": 1.0,
            },
            {
                "id": "rh.pial",
                "filename": "rh.pial",
                "name": "Right pial surface",
                "type": "surface",
                "role": "pial",
                "hemisphere": "right",
                "visible": False,
                "opacity": 1.0,
            },
        ],
        "is_job_running": False,
    }
    return GuiRuntime(), state


def test_gui_runtime_exposes_expected_llm_tools(runtime_case):
    service, state = runtime_case
    tools = service.available_tools(gui_state_key="tools", gui_state_override=state)
    names = {tool["function"]["name"] for tool in tools}

    assert {
        "freesurfer_lut",
        "case_file_tree",
        "read_stats",
        "gui_list_layers",
        "gui_load_layer",
        "gui_reorder_layer",
        "gui_remove_layer",
        "gui_set_layer_visibility",
        "gui_set_layer_display",
        "gui_apply_view_preset",
        "gui_move_cursor",
        "gui_focus_label",
    }.issubset(names)
    lut_tool = next(tool for tool in tools if tool["function"]["name"] == "freesurfer_lut")
    assert "volume_path" in lut_tool["function"]["parameters"]["properties"]


def test_gui_runtime_exposes_case_tools_without_gui_context():
    service = GuiRuntime()
    tools = service.available_tools(gui_state_key="empty", gui_state_override={})
    names = {tool["function"]["name"] for tool in tools}

    assert "gui_load_layer" in names
    assert "gui_reorder_layer" in names
    assert "gui_remove_layer" in names
    assert "gui_move_cursor" in names
    assert "gui_apply_view_preset" in names
    assert "read_stats" in names

    result = service.call_tool("gui_move_cursor", {"x": 1, "y": 2, "z": 3}, gui_state_override={}, gui_state_key="empty")
    assert "requires a loaded layer" in result


def test_gui_list_layers_reports_visibility_and_same_type_order(runtime_case):
    service, state = runtime_case

    result = service.call_tool("gui_list_layers", {}, gui_state_override=state, gui_state_key="list")
    layers = json.loads(result)

    assert [(layer["id"], layer["visible"], layer["type_order"]) for layer in layers] == [
        ("orig.mgz", True, 0),
        ("lh.pial", False, 0),
        ("rh.pial", False, 1),
    ]


def test_gui_load_layer_rejects_paths_outside_active_case(runtime_case):
    service, state = runtime_case

    for file_path in ("mri/orig.mgz", "/case/../other-case/mri/orig.mgz"):
        result = service.call_tool(
            "gui_load_layer",
            {"file_path": file_path},
            gui_state_override=state,
            gui_state_key="load",
        )
        assert result.startswith("Error:")


def test_gui_set_layer_display_rejects_type_mismatches(runtime_case):
    service, state = runtime_case
    scenarios = (
        (["lh.pial"], {"brightness": 10}, "brightness and contrast apply only"),
        (["orig.mgz"], {"surface_color_mode": "curvature"}, "surface_color_mode applies only"),
    )
    for layer_ids, updates, expected in scenarios:
        result = service.call_tool(
            "gui_set_layer_display",
            {"layer_ids": layer_ids, **updates},
            gui_state_override=state,
            gui_state_key="display",
        )
        assert expected in result


def test_gui_tool_override_mutations_are_visible_to_sync(runtime_case):
    service, state = runtime_case
    state_key = "gui-regression"

    result = service.call_tool(
        "gui_set_layer_visibility",
        {"changes": [{"layer_id": "lh.pial", "visible": True}]},
        gui_state_override={**state, "current_cursor": {"voxel": [1, 2, 3]}},
        gui_state_key=state_key,
    )
    response = service.sync_gui_state(state, gui_state_key=state_key)

    assert "visibility" in result
    assert response["commands"][0]["type"] == "set_layer_visibility"
    command_id = response["commands"][0]["id"]
    acknowledged = service.sync_gui_state(
        {**state, "acknowledged_command_ids": [command_id]},
        gui_state_key=state_key,
    )
    assert acknowledged["commands"] == []
    assert json.loads(service.call_tool(
        "gui_command_status",
        {"command_id": command_id},
        gui_state_key=state_key,
    ))["status"] == "acknowledged"


def test_gui_surface_preset_enqueues_a_frontend_command(runtime_case):
    service, state = runtime_case
    state_key = "gui-review-regression"

    result = service.call_tool(
        "gui_apply_view_preset",
        {"preset": "pial_surfaces"},
        gui_state_override=state,
        gui_state_key=state_key,
    )
    response = service.sync_gui_state(state, gui_state_key=state_key)
    assert "pial_surfaces" in result
    assert response["commands"][0]["payload"]["changes"] == [
        {"layer_id": "orig.mgz", "visible": True},
        {"layer_id": "lh.pial", "visible": True},
        {"layer_id": "rh.pial", "visible": True},
    ]


def test_gui_reorder_layer_enqueues_one_same_type_move(runtime_case):
    service, state = runtime_case
    state_key = "gui-reorder-regression"

    result = service.call_tool(
        "gui_reorder_layer",
        {
            "layer_type": "surface",
            "layer_id": "rh.pial",
            "target_layer_id": "lh.pial",
            "position": "before",
        },
        gui_state_override=state,
        gui_state_key=state_key,
    )
    response = service.sync_gui_state(state, gui_state_key=state_key)

    assert "within surface layers" in result
    assert response["commands"][0]["type"] == "reorder_layer"
    assert response["commands"][0]["payload"] == {
        "layer_type": "surface",
        "layer_id": "rh.pial",
        "target_layer_id": "lh.pial",
        "position": "before",
    }


def test_gui_reorder_layer_rejects_cross_type_move(runtime_case):
    service, state = runtime_case
    state_key = "cross-type-reorder"

    result = service.call_tool(
        "gui_reorder_layer",
        {
            "layer_type": "surface",
            "layer_id": "lh.pial",
            "target_layer_id": "orig.mgz",
            "position": "after",
        },
        gui_state_override=state,
        gui_state_key=state_key,
    )

    assert "Cross-type reordering is not allowed" in result


def test_freesurfer_lut_filters_results_to_labels_present_in_volume(runtime_case):
    from typing import Any, cast

    import nibabel as nib
    import numpy as np

    service, state = runtime_case
    case_dir = viewer_paths_module.local_output_root() / "workspaces" / state["workspace_id"] / "cases" / "case-a"
    volume_path = case_dir / "mri" / "labels.nii.gz"
    data = np.zeros((3, 3, 3), dtype=np.int16)
    data[0, 0, 0] = 17
    data[1, 1, 1] = 251
    nib_module = cast(Any, nib)
    nib_module.save(nib_module.Nifti1Image(data, affine=np.eye(4)), volume_path)

    result = service.call_tool(
        "freesurfer_lut",
        {"query": "corpus callosum", "volume_path": "/case/mri/labels.nii.gz"},
        gui_state_override=state,
        gui_state_key="lut",
    )

    assert "Filtered to 3 unique integer label ID(s)" in result
    assert "251\tCC_Posterior" in result
    assert "192\tCorpus_Callosum" not in result
    assert "1004\tctx-lh-corpuscallosum" not in result
