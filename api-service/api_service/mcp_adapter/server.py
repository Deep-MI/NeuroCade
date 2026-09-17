"""SDK transport for the local monolith. No model or additional worker is started."""

import json
import time
from collections import defaultdict, deque

from fastapi import HTTPException
from jsonschema import ValidationError
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from api_service.assistant.tools.definition import ToolRisk
from api_service.mcp_adapter.auth import authenticate, current_client, validate_local_headers
from api_service.mcp_adapter.identity import installation_id
from api_service.mcp_adapter.prompts import instructions
from api_service.mcp_adapter.service import invoke, public_schema, serialize, state_for, tool_builder
from backend_common.db import AssistantToolExecution, SessionLocal
from backend_common.settings import get_settings


def permitted(tool, client):
    return (not tool.risk.requires_confirmation and tool.risk != ToolRisk.gui) or (
        client.access == "standard" and get_settings().mcp_access == "standard"
    )


def create_adapter():
    server = Server(
        "neurocade",
        version="2.0.0",
        instructions=instructions(),
    )

    @server.list_tools()
    async def list_tools():
        with SessionLocal() as db:
            client, state = state_for(db, current_client.get())
            tools = [
                types.Tool(
                    name="neurocade_" + tool.name,
                    description=tool.description,
                    inputSchema=public_schema(tool),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=tool.risk == ToolRisk.read,
                        destructiveHint=tool.risk.requires_confirmation,
                        openWorldHint=False,
                    ),
                )
                for tool in tool_builder().discover(state)
                if permitted(tool, client)
            ]
            tools += [
                types.Tool(
                    name="neurocade_get_context",
                    description="Inspect connection scope and documentation versions.",
                    inputSchema={"type": "object", "properties": {"case_id": {"type": "string"}, "gui_session_id": {"type": "string"}}, "additionalProperties": False},
                ),
                types.Tool(
                    name="neurocade_get_invocation",
                    description="Check an external invocation or approval without resubmitting.",
                    inputSchema={
                        "type": "object",
                        "properties": {"invocation_id": {"type": "string"}},
                        "required": ["invocation_id"],
                        "additionalProperties": False,
                    },
                ),
            ]
            return tools

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            with SessionLocal() as db:
                client, _ = state_for(db, current_client.get())
                if name == "neurocade_get_context":
                    if set(arguments) - {"case_id", "gui_session_id"} or any(not isinstance(value, str) or not value for value in arguments.values()):
                        raise HTTPException(400, "Only optional case_id and gui_session_id strings are accepted")
                    client, state = state_for(db, client.id, arguments.get("case_id"), arguments.get("gui_session_id"))
                    builder = tool_builder()
                    state["gui_state"] = builder.load_gui_state(state)
                    state["tool_specs"] = [tool.as_openai_tool() for tool in builder.build(state)[0] if permitted(tool, client)]
                    import os

                    from api_service import browser_navigation
                    from api_service.documentation.tools import bundle
                    from api_service.runtime.gui_runtime import gui_runtime

                    data = {
                        "client_id": client.id,
                        "launch_id": os.environ.get("NEUROCADE_LAUNCH_ID", "development"),
                        "workspace_id": client.workspace_id,
                        "access": "read" if get_settings().mcp_access == "read" else client.access,
                        "adapter_version": "2.0.0",
                        "require_approval": client.require_approval,
                        "confirmation": "neurocade" if client.require_approval else "agent",
                        "case_id": state["case_id"],
                        "instructions": instructions(state),
                        "viewer_sessions": gui_runtime.gui_state_store.sessions(user_id=client.user_id, workspace_id=client.workspace_id),
                        "browser_sessions": browser_navigation.sessions(client.user_id, client.workspace_id),
                        "transfers": {"base_path": "/api/app/mcp/files", "upload": "PUT /upload?filename=...&idempotency_key=...&sha256=...&title=... (or case_id=...); raw binary body", "download": "GET /artifacts/{artifact_id} or /cases/{case_id}; supports Range and SHA-256", "authentication": "Bearer credential plus X-NeuroCade-Installation header; local stdio connector supplies these automatically"},
                        "installation_id": installation_id(),
                        "runtime": get_settings().neurocade_runtime,
                        "documentation": bundle()["versions"],
                    }
                elif name == "neurocade_get_invocation":
                    if set(arguments) != {"invocation_id"} or not isinstance(arguments["invocation_id"], str):
                        raise HTTPException(400, "invocation_id is required")
                    row = db.get(AssistantToolExecution, arguments["invocation_id"])
                    if row is None or row.client_id != client.id:
                        raise HTTPException(404, "Invocation not found")
                    data = serialize(row)
                elif name.startswith("neurocade_"):
                    data = await invoke(db, client.id, name.removeprefix("neurocade_"), arguments)
                else:
                    raise HTTPException(404, "Unknown tool")
                text = json.dumps(data, default=str)
                if len(text.encode()) > 200000:
                    raise HTTPException(413, "Result too large; narrow the query")
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=text)],
                    structuredContent=data,
                    isError=data.get("status") in {"failed", "ambiguous", "expired", "denied"},
                )
        except (HTTPException, ValidationError, ValueError, KeyError) as exc:
            detail = str(exc.detail) if isinstance(exc, HTTPException) else "Invalid tool arguments"
            data = {"status": "failed", "error": {"code": {400: "INVALID_INPUT", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 413: "RESULT_TOO_LARGE"}.get(getattr(exc, "status_code", 400), "INVALID_INPUT"), "message": detail}}
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data))], structuredContent=data, isError=True
            )

    manager = StreamableHTTPSessionManager(
        server,
        stateless=True,
        json_response=True,
        max_request_body_size=65536,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "localhost", "[::1]", "127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )
    recent = defaultdict(deque)
    active = defaultdict(int)

    async def endpoint(scope, receive, send):
        if scope["type"] != "http":
            return
        headers = Headers(scope=scope)
        try:
            validate_local_headers(headers)
            with SessionLocal() as db:
                identity = authenticate(db, headers.get("authorization", ""))
            now = time.monotonic()
            window = recent[identity]
            while window and window[0] < now - 60:
                window.popleft()
            if len(window) >= 120 or active[identity] >= 4:
                raise HTTPException(429, "MCP request limit reached")
            window.append(now)
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
            return
        token = current_client.set(identity)
        active[identity] += 1
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            active[identity] -= 1
            current_client.reset(token)

    return endpoint, manager
