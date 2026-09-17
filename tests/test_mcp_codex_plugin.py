"""Generated plugins reference a private credential and reuse the stdio bridge."""

import json

import pytest
from neurocade_mcp.plugin import build_plugin
from neurocade_mcp.setup import private_json, register


@pytest.fixture
def connection(tmp_path):
    path = tmp_path / "connection.json"
    private_json(path, {"url": "http://127.0.0.1:8000/mcp", "installation_id": "test",
                        "client_id": "test-client", "token": "ncmcp_secret_not_in_plugin"})
    return path


def test_package_is_private_credential_free_and_repeatable(connection, tmp_path):
    executable = tmp_path / "host connector"
    first = build_plugin(executable, connection)
    plugin = connection.parent / "plugins" / first["marketplace_name"] / "plugins/neurocade"
    version = json.loads((plugin / ".codex-plugin/plugin.json").read_text())["version"]
    mcp = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["neurocade"]
    assert mcp == {"command": str(executable), "args": ["connect", "--connection-file", str(connection)]}
    for source in plugin.rglob("*"):
        if source.is_file():
            assert "ncmcp_secret_not_in_plugin" not in source.read_text()
    assert build_plugin(executable, connection) == first
    assert json.loads((plugin / ".codex-plugin/plugin.json").read_text())["version"] == version
    obsolete = plugin / "skills/neurocade/obsolete.md"
    obsolete.write_text("removed from the next generated version")
    build_plugin(executable, connection)
    assert not obsolete.exists()
    build_plugin(tmp_path / "moved connector", connection)
    assert json.loads((plugin / ".codex-plugin/plugin.json").read_text())["version"] != version
    assert connection.stat().st_mode & 0o777 == 0o600


def test_register_uses_plugin_install_not_direct_mcp(connection, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("neurocade_mcp.setup.codex_binary", lambda: "/codex")
    monkeypatch.setattr("neurocade_mcp.setup.subprocess.run", lambda command, **kwargs: calls.append(command))
    package = register("codex-plugin", "unused", tmp_path / "connector", connection)
    assert package is not None
    assert calls == [
        ["/codex", "plugin", "marketplace", "add", package["marketplace_root"]],
        ["/codex", "plugin", "add", "neurocade@" + package["marketplace_name"]],
    ]


def test_package_rejects_insecure_credentials(connection, tmp_path):
    connection.chmod(0o644)
    with pytest.raises(ValueError, match="mode 600"):
        build_plugin(tmp_path / "connector", connection)
