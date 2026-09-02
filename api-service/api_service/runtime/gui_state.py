"""In-memory typed GUI state and acknowledged command queues."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

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
        "workspace_id": None,
        "case_id": None,
        "current_intensity_artifact_id": None,
        "current_intensity_volume": None,
        "layers": [],
        "commands": [],
        "acknowledged_commands": [],
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
    def __init__(self, *, ttl_seconds: int | None = None, max_entries: int | None = None) -> None:
        self._state_by_key: dict[str, dict[str, Any]] = {}
        self._last_access_by_key: dict[str, float] = {}
        self._ttl_seconds = max(
            1,
            ttl_seconds
            if ttl_seconds is not None
            else int(os.environ.get("GUI_STATE_TTL_SECONDS", "3600") or 3600),
        )
        self._max_entries = max(
            1,
            max_entries
            if max_entries is not None
            else int(os.environ.get("GUI_STATE_MAX_ENTRIES", "256") or 256),
        )
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        expired_keys = [
            key
            for key, last_access in self._last_access_by_key.items()
            if now - last_access > self._ttl_seconds
        ]
        for key in expired_keys:
            self._state_by_key.pop(key, None)
            self._last_access_by_key.pop(key, None)

    def _make_room_for_session(self) -> None:
        candidates = [
            (last_access, key)
            for key, last_access in self._last_access_by_key.items()
        ]
        if len(candidates) >= self._max_entries:
            _last_access, oldest_key = min(candidates)
            self._state_by_key.pop(oldest_key, None)
            self._last_access_by_key.pop(oldest_key, None)

    def state_for_key(self, state_key: str) -> dict[str, Any]:
        normalized_key = str(state_key).strip()
        if not normalized_key:
            raise ValueError("A GUI session key is required")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            state = self._state_by_key.get(normalized_key)
            if state is None:
                self._make_room_for_session()
                state = new_gui_state()
                self._state_by_key[normalized_key] = state
            self._last_access_by_key[normalized_key] = now
            return state

    def fetch(self, *, gui_state_key: str) -> dict[str, Any]:
        state = self.state_for_key(gui_state_key)
        return {key: value for key, value in state.items() if key not in {"commands", "acknowledged_commands"}}

    def sync(self, payload: dict, *, gui_state_key: str) -> dict[str, Any]:
        gui_state = self.state_for_key(gui_state_key)
        previous_case_id = gui_state.get("case_id")
        gui_state["commands"] = _active_commands(gui_state)

        acknowledged = {
            str(command_id)
            for command_id in payload.get("acknowledged_command_ids", [])
            if command_id
        }
        if acknowledged:
            acknowledged_at = datetime.now(UTC).isoformat()
            completed = [
                {**command, "acknowledged_at": acknowledged_at}
                for command in gui_state.get("commands", [])
                if command.get("id") in acknowledged
            ]
            gui_state["acknowledged_commands"] = [
                *gui_state.get("acknowledged_commands", []),
                *completed,
            ][-MAX_QUEUED_GUI_COMMANDS:]
            gui_state["commands"] = [
                command
                for command in gui_state.get("commands", [])
                if command.get("id") not in acknowledged
            ]

        for key in (
            "is_job_running",
            "workspace_id",
            "case_id",
            "current_intensity_artifact_id",
            "current_intensity_volume",
            "current_cursor",
            "layers",
        ):
            if key in payload:
                gui_state[key] = payload.get(key)

        case_context_changed = (
            "case_id" in payload
            and payload.get("case_id") != previous_case_id
        )
        if case_context_changed:
            gui_state["is_job_running"] = False
            gui_state["commands"] = []
            gui_state["acknowledged_commands"] = []

        current_state = {
            key: value for key, value in gui_state.items() if key not in {"commands", "acknowledged_commands"}
        }
        return {
            "status": "success",
            "current_state": current_state,
            "commands": list(gui_state.get("commands", [])),
        }
