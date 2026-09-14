"""Shared case navigation for workspace chat and external agents."""

from urllib.parse import quote

from api_service import browser_navigation
from api_service.assistant.tools.definition import ToolResult, ToolRisk
from api_service.assistant.tools.registration import ToolRegistration
from api_service.helpers import get_case_for_user


def navigation_tools(state):
    def execute(state, _execution, arguments):
        case, _, _, _ = get_case_for_user(state["db"], arguments["case_id"], state["context"].user.id,
                                         workspace_id=state["workspace_id"])
        session_id = arguments.get("browser_session_id")
        if not session_id and not str(state.get("gui_session_id", "")).startswith("mcp-"):
            session_id = state.get("gui_session_id")
        result = browser_navigation.open_case(state["context"].user.id, case.workspace_id, case.id, session_id)
        return ToolResult.structured({**result, "case_id": case.id,
            "path": f"/workspaces/{quote(case.workspace_id, safe='')}/cases/{quote(case.id, safe='')}"})

    return [ToolRegistration("open_case", "Open a specific authorized case in the user's NeuroCade browser. Does not run an analysis. Uses the current local chat tab or the only available browser tab; if ambiguous, select browser_session_id from the returned browser_sessions. Reports queued, not confirmed open. Do not supply the MCP envelope gui_session_id for this tool.",
        {"type": "object", "properties": {"case_id": {"type": "string", "minLength": 1},
         "browser_session_id": {"type": "string", "minLength": 1}}, "required": ["case_id"], "additionalProperties": False},
        execute, risk=ToolRisk.gui).bind(state)]
