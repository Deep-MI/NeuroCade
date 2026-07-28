"""In-memory typed GUI state and acknowledged command queues."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

DEFAULT_GUI_STATE_KEY = "default"
GUI_COMMAND_TTL = timedelta(minutes=5)
MAX_QUEUED_GUI_COMMANDS = 100


def _command_is_active(command: dict[str, Any], now: datetime) -> bool:
    try:
        created_at = datetime.fromisoformat(str(command["created_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return now - created_at <= GUI_COMMAND_TTL


def _active_commands(gui_state: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    current_time = now or datetime.now(UTC)
    return [
        command
        for command in gui_state.get("commands", [])
        if isinstance(command, dict) and _command_is_active(command, current_time)
    ][-MAX_QUEUED_GUI_COMMANDS:]


def new_gui_state() -> dict[str, Any]:
    return {
        "is_job_running": False,
        "current_workspace_id": None,
        "current_case_id": None,
        "current_intensity_artifact_id": None,
        "current_intensity_volume": None,
        "layers": [],
        "commands": [],
    }


def enqueue_gui_command(gui_state: dict[str, Any], command_type: str, payload: dict[str, Any]) -> str:
    """Append an idempotent frontend command and return its acknowledgement ID."""
    command_id = uuid4().hex
    now = datetime.now(UTC)
    commands = _active_commands(gui_state, now)
    commands.append(
        {
            "id": command_id,
            "type": command_type,
            "payload": payload,
            "created_at": now.isoformat(),
            "expires_at": (now + GUI_COMMAND_TTL).isoformat(),
        }
    )
    gui_state["commands"] = commands[-MAX_QUEUED_GUI_COMMANDS:]
    return command_id


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
        state = self.state_for_key(gui_state_key)
        return {key: value for key, value in state.items() if key != "commands"}

    def sync(self, payload: dict, *, gui_state_key: str | None = None) -> dict[str, Any]:
        gui_state = self.state_for_key(gui_state_key)
        previous_case_id = gui_state.get("current_case_id")
        gui_state["commands"] = _active_commands(gui_state)

        acknowledged = {
            str(command_id)
            for command_id in payload.get("acknowledged_command_ids", [])
            if command_id
        }
        if acknowledged:
            gui_state["commands"] = [
                command
                for command in gui_state.get("commands", [])
                if command.get("id") not in acknowledged
            ]

        for key in (
            "is_job_running",
            "current_workspace_id",
            "current_case_id",
            "current_intensity_artifact_id",
            "current_intensity_volume",
            "current_cursor",
            "layers",
        ):
            if key in payload:
                gui_state[key] = payload.get(key)

        case_context_changed = (
            "current_case_id" in payload
            and payload.get("current_case_id") != previous_case_id
        )
        if case_context_changed:
            gui_state["is_job_running"] = False
            gui_state["commands"] = []

        current_state = {
            key: value for key, value in gui_state.items() if key != "commands"
        }
        return {
            "status": "success",
            "current_state": current_state,
            "commands": list(gui_state.get("commands", [])),
        }
