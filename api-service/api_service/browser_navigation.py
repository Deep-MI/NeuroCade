"""Ephemeral, user-scoped browser navigation; never launches a server browser."""

import time
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException

_lock = RLock()
_sessions: dict[tuple[str, str], dict] = {}


def _prune():
    for key, value in list(_sessions.items()):
        if value["seen"] < time.monotonic() - 30:
            del _sessions[key]


def sessions(user_id, workspace_id):
    with _lock:
        _prune()
        return [{"browser_session_id": sid} for (uid, sid), value in _sessions.items()
                if uid == user_id and value["workspace_id"] in {None, workspace_id}]


def sync(user_id, session_id, workspace_id, acknowledged):
    with _lock:
        _prune()
        state = _sessions.setdefault((user_id, session_id), {})
        command = state.get("command")
        if command and (command["id"] == acknowledged or command["expires"] < time.monotonic()):
            command = None
        state.update(seen=time.monotonic(), workspace_id=workspace_id, command=command)
        return {"command": {k: v for k, v in command.items() if k != "expires"} if command else None}


def open_case(user_id, workspace_id, case_id, session_id=None):
    with _lock:
        available = sessions(user_id, workspace_id)
        if session_id is None and len(available) == 1:
            session_id = available[0]["browser_session_id"]
        if session_id is None or {"browser_session_id": session_id} not in available:
            return {"status": "browser_required", "browser_sessions": available,
                    "message": "Open NeuroCade in this workspace. If multiple tabs are available, select browser_session_id and retry."}
        state = _sessions[(user_id, session_id)]
        if state.get("command") and state["command"]["expires"] > time.monotonic():
            if state["command"]["case_id"] != case_id:
                raise HTTPException(409, "A browser navigation is already pending; wait before opening another case")
        else:
            state["command"] = {"id": str(uuid4()), "case_id": case_id, "workspace_id": workspace_id,
                                "expires": time.monotonic() + 15}
        return {"status": "queued", "command_id": state["command"]["id"],
                "browser_session_id": session_id, "message": "Case navigation queued for the user's browser; not yet confirmed open."}
