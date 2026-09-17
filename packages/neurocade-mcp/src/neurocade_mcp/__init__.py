"""Client-side stdio bridge. Never imports or starts the NeuroCade backend."""

import argparse
import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def load_connection(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Connection file must be a regular private file")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Connection file must be owned by you and mode 600; run neurocade-mcp install FILE")
    data = json.loads(path.read_text())
    url = urlsplit(data["url"])
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.path != "/mcp"
        or url.username
        or url.query
        or url.fragment
    ):
        raise ValueError("Only local NeuroCade MCP endpoints are supported")
    if not isinstance(data.get("installation_id"), str) or not data["installation_id"]:
        raise ValueError("Legacy connection file: pair once again in NeuroCade and revoke the old connection")
    return data


def validate_identity(data, context):
    details = context.structuredContent or {}
    if context.isError or details.get("client_id") != data["client_id"] or details.get("installation_id") != data["installation_id"]:
        raise ValueError("Mismatched installation or client; check the connection in NeuroCade")
    return details


def proxy_server(initialized):
    return Server("neurocade", version="1.0.0", instructions=initialized.instructions)


async def connect(path, check=False):
    data = load_connection(path)
    async with streamablehttp_client(data["url"], headers={"Authorization": "Bearer " + data["token"]}) as (read, write, _):  # noqa: SIM117
        async with ClientSession(read, write) as remote:
            initialized = await remote.initialize()
            context = await remote.call_tool("neurocade_get_context", {})
            details = validate_identity(data, context)
            if check:
                available = (await remote.list_tools()).tools
                if not any(tool.name == "neurocade_tool_call" for tool in available) and details["access"] == "standard":
                    raise ValueError("Workflow tools unavailable; rebuild NeuroCade")
                print("Connected to NeuroCade; workspace " + details["workspace_id"])
                return
            server = proxy_server(initialized)

            @server.list_tools()
            async def list_tools():
                from neurocade_mcp.transfers import file_tools
                return (await remote.list_tools()).tools + file_tools(details["access"])

            @server.call_tool(validate_input=False)
            async def call_tool(name, arguments):
                if name in {"neurocade_upload_case", "neurocade_download_file"}:
                    from neurocade_mcp.errors import TransferError
                    from neurocade_mcp.transfers import download, upload
                    try:
                        operation = upload if name == "neurocade_upload_case" else download
                        result = await asyncio.to_thread(operation, data, arguments)
                        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], structuredContent=result)
                    except TransferError as exc:
                        return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], structuredContent={"error": exc.details}, isError=True)
                    except httpx.TransportError:
                        message = "Transfer connection interrupted; the upload outcome may be unknown. Inspect the case and retry with the SAME upload parameters and idempotency_key, or resume the download at the same destination. Do not rewrite the scan based on a transport failure."
                        return types.CallToolResult(content=[types.TextContent(type="text", text=message)], structuredContent={"error": {"code": "TRANSFER_OUTCOME_UNKNOWN", "message": message, "retryable": True}}, isError=True)
                    except (ValueError, OSError) as exc:
                        return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], isError=True)
                    except Exception:
                        return types.CallToolResult(content=[types.TextContent(type="text", text="Transfer interrupted. Retry using the same upload key or download destination; credentials were not logged.")], isError=True)
                return await call_remote_tool(remote, name, arguments)

            async with stdio_server() as (stdin, stdout):
                await server.run(stdin, stdout, server.create_initialization_options())


async def call_remote_tool(remote, name, arguments):
    from mcp.shared.exceptions import McpError

    try:
        return await remote.call_tool(name, arguments)
    except (httpx.TransportError, TimeoutError, McpError) as exc:
        if isinstance(exc, McpError) and exc.error.code != httpx.codes.REQUEST_TIMEOUT:
            raise
        message = "Tool request timed out or lost its connection. Submission outcome is UNKNOWN, not proof that no run was queued. Check run status and recent runs. If retrying a mutation, reuse exactly the same arguments and idempotency_key; never generate a new key merely because of a timeout."
        return types.CallToolResult(content=[types.TextContent(type="text", text=message)], structuredContent={"error": {"code": "TOOL_OUTCOME_UNKNOWN", "message": message}}, isError=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("connect", "check"):
        sub.add_parser(command).add_argument("--connection-file", required=True)
    sub.add_parser("install").add_argument("file")
    sub.add_parser("connect-paired").add_argument("--pairing-file", required=True)
    setup_parser = sub.add_parser("setup")
    setup_parser.add_argument("--url", default="http://localhost:8000")
    setup_parser.add_argument("--client", choices=["chatgpt", "codex", "codex-plugin", "claude-desktop", "claude-code", "manual"], required=True)
    setup_parser.add_argument("--pairing-code")
    setup_parser.add_argument("--pairing-id")
    setup_parser.add_argument("--installation-id")
    setup_parser.add_argument("--workspace")
    setup_parser.add_argument("--create-workspace")
    setup_parser.add_argument("--list-workspaces", action="store_true")
    setup_parser.add_argument("--require-approval", action="store_true")
    setup_parser.add_argument("--connection-dir")
    setup_parser.add_argument("--config-path")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            from neurocade_mcp.setup import setup
            setup(args)
        elif args.command == "connect-paired":
            from neurocade_mcp.pairing import paired_connection, read_pairing
            asyncio.run(connect(paired_connection(read_pairing(args.pairing_file))))
        elif args.command == "install":
            path = Path(args.file).absolute()
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
                raise ValueError("Connection file must be a regular file owned by you")
            path.chmod(0o600)
            load_connection(path)
            print(
                json.dumps(
                    {
                        "mcpServers": {
                            "neurocade": {
                                "command": str(Path(sys.executable).parent / "neurocade-mcp"),
                                "args": ["connect", "--connection-file", str(path)],
                            }
                        }
                    },
                    indent=2,
                )
            )
        else:
            asyncio.run(connect(args.connection_file, check=args.command == "check"))
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        # Exceptions can contain HTTP headers; never echo credentials.
        print(
            "NeuroCade connection failed. Check the endpoint and private file. Legacy launch-only files require one-time re-pairing; revoke the old connection in NeuroCade.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
