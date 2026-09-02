"""Runtime tool schema discovery and dispatch."""

from __future__ import annotations

from typing import Any

from api_service.runtime.gui_state import GuiStateStore
from api_service.runtime_tools import (
    STATIC_TOOLS,
    get_dynamic_gui_tools,
    handle_case_file_tree,
    handle_gui_apply_view_preset,
    handle_gui_command_status,
    handle_gui_focus_label,
    handle_gui_list_layers,
    handle_gui_load_layer,
    handle_gui_move_cursor,
    handle_gui_remove_layer,
    handle_gui_reorder_layer,
    handle_gui_set_layer_display,
    handle_gui_set_layer_visibility,
    handle_lut_lookup,
    handle_read_stats,
)


def text_result(blocks: list[Any]) -> str:
    return "\n".join(str(getattr(block, "text", "")) for block in blocks if hasattr(block, "text"))


class RuntimeToolDispatcher:
    def __init__(self, gui_state_store: GuiStateStore) -> None:
        self.gui_state_store = gui_state_store

    def available_tools(
        self,
        *,
        gui_state_key: str,
        gui_state_override: dict | None = None,
    ) -> list[dict[str, Any]]:
        base_gui_state = self.gui_state_store.state_for_key(gui_state_key)
        effective_gui_state = base_gui_state if gui_state_override is None else {**base_gui_state, **gui_state_override}
        return [tool.as_openai_tool() for tool in list(STATIC_TOOLS) + get_dynamic_gui_tools(effective_gui_state)]

    def call_tool(
        self,
        name: str,
        arguments: dict,
        gui_state_override: dict | None = None,
        *,
        gui_state_key: str,
    ) -> str:
        base_gui_state = self.gui_state_store.state_for_key(gui_state_key)
        if gui_state_override is not None:
            base_gui_state.update(gui_state_override)
        effective_gui_state = base_gui_state
        gui_tools = {
            "gui_move_cursor": handle_gui_move_cursor,
            "gui_command_status": handle_gui_command_status,
            "gui_focus_label": handle_gui_focus_label,
            "gui_list_layers": handle_gui_list_layers,
            "gui_load_layer": handle_gui_load_layer,
            "gui_reorder_layer": handle_gui_reorder_layer,
            "gui_remove_layer": handle_gui_remove_layer,
            "gui_set_layer_visibility": handle_gui_set_layer_visibility,
            "gui_set_layer_display": handle_gui_set_layer_display,
            "gui_apply_view_preset": handle_gui_apply_view_preset,
        }
        direct_tools = {
            "case_file_tree": handle_case_file_tree,
            "freesurfer_lut": handle_lut_lookup,
            "read_stats": handle_read_stats,
        }
        if name in gui_tools:
            return text_result(gui_tools[name](arguments, effective_gui_state))
        if name in direct_tools:
            return text_result(direct_tools[name](arguments, effective_gui_state))
        raise ValueError(f"Unknown tool: {name}")
