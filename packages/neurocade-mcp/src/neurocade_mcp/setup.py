"""Deterministic local pairing and client registration; credentials never printed."""

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Refusing to replace a symbolic link")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".neurocade-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def codex_binary():
    binary = shutil.which("codex")
    if not binary:
        for candidate in ("/Applications/ChatGPT.app/Contents/Resources/codex", "/Applications/Codex.app/Contents/Resources/codex"):
            if Path(candidate).is_file():
                binary = candidate
                break
    if not binary:
        raise ValueError("Codex executable not found; install the local OpenAI desktop app or Codex CLI")
    return binary


def register(target, name, executable, connection, config_path=None):
    command = [str(executable), "connect", "--connection-file", str(connection)]
    if target == "codex-plugin":
        from neurocade_mcp.plugin import build_plugin

        binary = codex_binary()
        package = build_plugin(executable, connection)
        subprocess.run([binary, "plugin", "marketplace", "add", package["marketplace_root"]], check=True, capture_output=True)
        subprocess.run([binary, "plugin", "add", "neurocade@" + package["marketplace_name"]], check=True, capture_output=True)
        return package
    if target in {"codex", "chatgpt"}:
        binary = codex_binary()
        subprocess.run([binary, "mcp", "add", name, "--", *command], check=True, capture_output=True)
    elif target == "claude-code":
        binary = shutil.which("claude")
        if not binary:
            raise ValueError("Claude Code executable not found")
        # add-json rejects existing names; use the same definition when already present.
        existing = subprocess.run([binary, "mcp", "get", name], capture_output=True)
        if existing.returncode == 0 and str(connection).encode() in existing.stdout and str(executable).encode() in existing.stdout:
            return
        if existing.returncode == 0:
            raise ValueError("Claude Code already has this connection name. Remove it with claude mcp remove NAME, then rerun setup")
        subprocess.run([binary, "mcp", "add", "--scope", "user", "--transport", "stdio", name, "--", *command], check=True, capture_output=True)
    elif target == "claude-desktop":
        if config_path:
            path = Path(config_path).expanduser()
        elif sys.platform == "darwin":
            path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
        elif sys.platform == "win32":
            path = Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json"
        else:
            raise ValueError("Provide --config-path for this Claude Desktop installation")
        if path.is_symlink():
            raise ValueError("Claude configuration must not be a symbolic link")
        config = json.loads(path.read_text()) if path.exists() else {}
        if path.exists():
            private_json(path.with_suffix(".json.neurocade-backup"), config)
        config.setdefault("mcpServers", {})[name] = {"command": command[0], "args": command[1:]}
        private_json(path, config)
    elif target != "manual":
        raise ValueError("Choose a supported local client; hosted sessions cannot launch a local connector")
    return None


def register_connection(target, executable, pairing_path, saved, config_path=None):
    if target == "manual":
        name = "neurocade-" + saved["client_id"]
        path = Path(pairing_path)
        return name, path, register(target, name, executable, path, config_path)
    # Pairing grants are single-use; the installed adapter has a stable identity
    # per installation, user, workspace and client. New grants update its file.
    scope = [saved["installation_id"], saved["user_id"], saved["workspace_id"], target]
    slot = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:24]
    name = "neurocade-" + slot
    path = Path(pairing_path).parent / "registrations" / (slot + ".json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        private_json(path, saved)
        package = register(target, name, executable, path, config_path)
        return name, path, package
    finally:
        os.close(fd)


def setup(args):
    from neurocade_mcp import connect, load_connection
    from neurocade_mcp.pairing import paired_connection

    parsed = urlsplit(args.url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("Setup requires a local NeuroCade http://localhost:PORT URL")
    url = args.url.rstrip("/")
    supplied = [getattr(args, key, None) for key in ("pairing_code", "pairing_id", "installation_id")]
    if any(supplied) and not all(supplied):
        raise ValueError("Use the complete setup prompt generated by NeuroCade.")
    if all(supplied):
        if args.workspace or args.create_workspace or args.list_workspaces or args.require_approval:
            raise ValueError("The pairing code already fixes the workspace and approval policy. Use the generated prompt unchanged.")
        grant = {"code": args.pairing_code, "pairing_id": args.pairing_id,
                 "installation_id": args.installation_id, "url": url + "/mcp"}
    else:
        headers = {"X-NeuroCade-UI": "1"}
        if os.environ.get("NEUROCADE_UI_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["NEUROCADE_UI_TOKEN"]
        with httpx.Client(base_url=url + "/api/app/", headers=headers, timeout=20, trust_env=False) as client:
            response = client.get("workspaces")
            if response.status_code in {401, 403}:
                raise ValueError("Generate a setup prompt in NeuroCade’s Connect external agents page while signed in.")
            response.raise_for_status()
            workspaces = response.json()
            if args.list_workspaces:
                print(json.dumps({"workspaces": workspaces}, indent=2))
                return
            if args.create_workspace:
                matches = [w for w in workspaces if w["name"] == args.create_workspace]
                if not matches:
                    response = client.post("workspaces", json={"name": args.create_workspace})
                    response.raise_for_status()
                    matches = [response.json()]
            else:
                matches = [w for w in workspaces if args.workspace in {w["id"], w["name"]}] if args.workspace else workspaces
            if len(matches) != 1:
                print(json.dumps({"status": "choose_workspace", "workspaces": [{"id": w["id"], "name": w["name"]} for w in workspaces]}, indent=2))
                raise ValueError("Choose one workspace and rerun with --workspace ID; no credential has been created")
            response = client.post("mcp/pairings", json={"name": args.client, "workspace_id": matches[0]["id"],
                                   "access": "standard", "require_approval": args.require_approval})
            response.raise_for_status()
            grant = response.json()
    path = paired_connection(grant, args.connection_dir)
    saved = load_connection(path)
    asyncio.run(connect(path, check=True))
    executable = Path(sys.executable).parent / "neurocade-mcp"
    name, path, package = register_connection(args.client, executable, path, saved, args.config_path)
    if package:
        print(json.dumps({"plugin": package, "next_step": "Start a new local task to use the NeuroCade plugin."}, indent=2))
    print(json.dumps({"status": "registered" if args.client != "manual" else "paired", "server_name": name,
                     "command": str(executable), "args": ["connect", "--connection-file", str(path)],
                     "next_step": "Reload MCP tools or start a new local task. Call neurocade_get_context to verify the workspace. Confirm with the user before starting analyses."}, indent=2))
