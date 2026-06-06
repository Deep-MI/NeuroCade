"""In-memory GUI state store for runtime tool sessions."""

from __future__ import annotations

from typing import Any

DEFAULT_GUI_STATE_KEY = "default"


def new_gui_state() -> dict[str, Any]:
    return {
        "is_job_running": False,
        "has_valid_segmentation": False,
        "current_workspace_id": None,
        "current_case_id": None,
        "loaded_volume_names": [],
        "visible_volumes": [],
        "current_intensity_artifact_id": None,
        "current_intensity_volume": None,
    }


class GuiStateStore:
    def __init__(self) -> None:
        self._state_by_key: dict[str, dict[str, Any]] = {DEFAULT_GUI_STATE_KEY: new_gui_state()}

    def state_for_key(self, state_key: str | None = None) -> dict[str, Any]:
        normalized_key = str(state_key or DEFAULT_GUI_STATE_KEY).strip() or DEFAULT_GUI_STATE_KEY
        state = self._state_by_key.get(normalized_key)
        if state is None:
            state = new_gui_state()
            self._state_by_key[normalized_key] = state
        return state

    def fetch(self, *, gui_state_key: str | None = None) -> dict[str, Any]:
        return dict(self.state_for_key(gui_state_key))

    def sync(self, payload: dict, *, gui_state_key: str | None = None) -> dict[str, Any]:
        gui_state = self.state_for_key(gui_state_key)
        previous_case_id = gui_state.get("current_case_id")
        requested_run = gui_state.get("requested_run_fastsurfer")

        for key in (
            "is_job_running",
            "has_valid_segmentation",
            "current_workspace_id",
            "current_case_id",
            "loaded_volumes",
            "loaded_volume_names",
            "visible_volumes",
            "current_intensity_artifact_id",
            "current_intensity_volume",
            "current_cursor",
        ):
            if key in payload:
                default_value = [] if key in {"loaded_volumes", "loaded_volume_names", "visible_volumes"} else payload.get(key)
                gui_state[key] = payload.get(key) or default_value
        if not gui_state.get("loaded_volume_names") and gui_state.get("loaded_volumes"):
            gui_state["loaded_volume_names"] = list(gui_state.get("loaded_volumes") or [])
        if not gui_state.get("loaded_volumes") and gui_state.get("loaded_volume_names"):
            gui_state["loaded_volumes"] = list(gui_state.get("loaded_volume_names") or [])

        case_context_changed = "current_case_id" in payload and payload.get("current_case_id") != previous_case_id
        if case_context_changed:
            gui_state["is_job_running"] = False
            if gui_state.get("current_case_id") is None:
                gui_state.pop("requested_run_fastsurfer", None)

        response = {"status": "success", "current_state": gui_state}
        for key in (
            "requested_cursor_position",
            "requested_load_volume",
            "requested_close_volume",
            "requested_close_volumes",
            "requested_select_volumes",
            "requested_adjust_display",
        ):
            if key in gui_state:
                response[key] = gui_state.pop(key)

        if requested_run is not None:
            target_case_id = requested_run.get("case_id")
            if target_case_id is None or payload.get("current_case_id") == target_case_id:
                response["requested_run_fastsurfer"] = requested_run
                gui_state.pop("requested_run_fastsurfer", None)

        return response
