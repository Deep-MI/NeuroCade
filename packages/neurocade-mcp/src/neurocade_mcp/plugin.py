"""Workspace-specific Codex packaging around the shared stdio connector."""

import hashlib
import json
import shutil
from pathlib import Path


def build_plugin(executable: Path, connection: Path) -> dict:
    from neurocade_mcp import load_connection
    from neurocade_mcp.setup import private_json

    # Validate credentials without copying them into the package or marketplace.
    load_connection(connection)
    connection = connection.absolute()
    slot = hashlib.sha256(str(connection).encode()).hexdigest()[:16]
    marketplace_name = "neurocade-" + slot
    root = connection.parent / "plugins" / marketplace_name
    plugin = root / "plugins" / "neurocade"
    template = Path(__file__).with_name("plugin_template") / "neurocade"
    mcp = {"mcpServers": {"neurocade": {
        "command": str(executable.absolute()),
        "args": ["connect", "--connection-file", str(connection)],
    }}}
    # Content-based version changes make reinstalls pick up updated skills/config.
    digest = hashlib.sha256(json.dumps(mcp, sort_keys=True).encode())
    for source in sorted(template.rglob("*")):
        if source.is_file():
            digest.update(source.relative_to(template).as_posix().encode())
            digest.update(source.read_bytes())
    manifest = json.loads((template / ".codex-plugin/plugin.json").read_text())
    manifest["version"] = "1.0.0+codex." + digest.hexdigest()[:16]
    marketplace = {
        "name": marketplace_name,
        "interface": {"displayName": "NeuroCade local workspace"},
        "plugins": [{
            "name": "neurocade",
            "source": {"source": "local", "path": "./plugins/neurocade"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    }
    # This is an app-owned generated directory, never a user's existing marketplace.
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Plugin directory must not contain symbolic links")
    # The target is app-owned and registration serializes rebuilds. Replace the
    # generated plugin rather than overlaying it, so removed template files do
    # not survive an upgrade.
    if plugin.exists():
        shutil.rmtree(plugin)
    shutil.copytree(template, plugin)
    private_json(plugin / ".codex-plugin/plugin.json", manifest)
    private_json(plugin / ".mcp.json", mcp)
    private_json(root / ".agents/plugins/marketplace.json", marketplace)
    return {"marketplace_name": marketplace_name, "marketplace_root": str(root),
            "marketplace_file": str(root / ".agents/plugins/marketplace.json"), "plugin_path": str(plugin)}
