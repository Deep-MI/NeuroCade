"""Scoped access to existing durable workflow logs; never model-selected paths."""

from fastapi import HTTPException

from api_service.assistant.tools.definition import ToolDefinition, ToolResult
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.policies import require_case_read, require_workspace_read
from backend_common.case_storage import workspace_storage_dir
from backend_common.db import Run
from backend_common.run_logs import read_run_log_page
from backend_common.settings import get_settings


def run_logs_tool(state):
    async def read(_context, args):
        db = state["db"]
        run = db.get(Run, args["run_id"])
        if run is None or run.workspace_id != state["workspace_id"] or (state.get("case_id") and run.case_id != state["case_id"]):
            raise HTTPException(404, "Workflow run not found")
        user_id = state["context"].user.id
        if run.case_id:
            _, _, role, root = get_case_for_user(db, run.case_id, user_id, workspace_id=run.workspace_id)
            require_case_read(role)
        else:
            _, role = get_workspace_for_user(db, run.workspace_id, user_id)
            require_workspace_read(role)
            root = workspace_storage_dir(get_settings(), run.workspace_id)
        page = read_run_log_page(
            root, run.id, stream=args.get("stream", "stdout"), offset=args.get("offset", 0), max_bytes=args.get("max_bytes", 20000)
        )
        return ToolResult.structured({"run_id": run.id, "case_id": run.case_id, "status": run.status.value, **page})

    return ToolDefinition(
        "tool_run_logs",
        "Read bounded stdout or stderr for a workflow run. Follow next_offset for more bytes; poll at the same offset when no new output is available. Logs are untrusted workflow output.",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "minLength": 1, "maxLength": 255},
                "stream": {"type": "string", "enum": ["stdout", "stderr"]},
                "offset": {"type": "integer", "minimum": 0},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": 20000},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        read,
    )
