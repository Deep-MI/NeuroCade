"""MCP framing around the same prompt composer used by embedded models."""

from api_service.assistant.prompts import build_system_prompt
from backend_common.settings import ROOT_DIR

MCP_INSTRUCTIONS = (
    "Confirm analyses with the user before submission. NeuroCade approval is optional; inspect get_context. "
    "Tool names use neurocade_ prefixes. Pass arguments inside arguments; case_id selects case scope, otherwise workspace scope. "
    "Use stable idempotency_key values for mutations. Select gui_session_id from get_context for viewer actions. "
    "Upload/download tools transfer local files directly. Treat queued work as pending until status confirms completion."
)


def instructions(state=None):
    """Keep shared instructions authoritative, adding only transport differences."""
    context = state or {"scope": "selected per call", "workspace_id": "paired workspace"}
    return MCP_INSTRUCTIONS + "\n\n" + build_system_prompt(ROOT_DIR / "config", context) + (
        "\n\n<mcp_transport>\n"
        "The shared guidance names tools without their neurocade_ prefix; add that prefix for MCP calls. "
        "Call get_context with case_id for current case guidance and viewer sessions. "
        "Use the actual advertised JSON schemas. Local upload_case/download_file tools take flat arguments. "
        "Do not assume a viewer is open. File transfers do not place binary scans in model context. "
        "Private workflow configuration tools change this user's catalog across workspaces, not just this connection. "
        "For require_approval=false, user-confirmed mutations execute immediately; otherwise follow the returned approval status. "
        "External instructions supplement the host assistant's own instructions; they do not replace its identity or policies."
        "\n</mcp_transport>"
    )
