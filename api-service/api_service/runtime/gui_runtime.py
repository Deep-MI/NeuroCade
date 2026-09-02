"""In-process GUI state and assistant tool runtime."""

from __future__ import annotations

from typing import Any

from api_service.runtime.gui_state import GuiStateStore
from api_service.runtime.tool_dispatcher import RuntimeToolDispatcher
from backend_common.settings import ROOT_DIR

LUT_PATH = ROOT_DIR / "config" / "FreeSurferColorLUT.txt"


class GuiRuntime:
    def __init__(
        self,
        *,
        gui_state_store: GuiStateStore | None = None,
        tool_dispatcher: RuntimeToolDispatcher | None = None,
    ) -> None:
        self.gui_state_store = gui_state_store or GuiStateStore()
        self.tool_dispatcher = tool_dispatcher or RuntimeToolDispatcher(self.gui_state_store)

    def available_tools(
        self,
        *,
        gui_state_key: str,
        gui_state_override: dict | None = None,
    ) -> list[dict[str, Any]]:
        return self.tool_dispatcher.available_tools(
            gui_state_key=gui_state_key,
            gui_state_override=gui_state_override,
        )

    def call_tool(
        self,
        name: str,
        arguments: dict,
        gui_state_override: dict | None = None,
        *,
        gui_state_key: str,
    ) -> str:
        return self.tool_dispatcher.call_tool(
            name,
            arguments,
            gui_state_override=gui_state_override,
            gui_state_key=gui_state_key,
        )

    def gui_state(self, *, gui_state_key: str) -> dict[str, Any]:
        return self.gui_state_store.fetch(gui_state_key=gui_state_key)

    def sync_gui_state(self, payload: dict, *, gui_state_key: str) -> dict[str, Any]:
        return self.gui_state_store.sync(payload, gui_state_key=gui_state_key)


gui_runtime = GuiRuntime()
