"""Desktop bundle safety and launcher failures without touching Claude settings."""

import io
import json
import shutil
import subprocess
from zipfile import ZipFile

import pytest
from api_service.mcp_adapter.extension import desktop_bundle
from fastapi import HTTPException


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("NEUROCADE_MCP_HOST_EXECUTABLE", "/missing/NeuroCade/.runtime/bridge-venv/bin/neurocade-mcp")
    archive = desktop_bundle({"url": "http://127.0.0.1:8000/mcp", "installation_id": "installation-test", "pairing_id": "p", "code": "ncpair_do_not_log"})
    with ZipFile(io.BytesIO(archive)) as zipped:
        zipped.extractall(tmp_path)
    return tmp_path, archive


def test_bundle_has_no_credentials_and_pins_host_connector(bundle):
    path, archive = bundle
    with ZipFile(io.BytesIO(archive)) as zipped:
        assert set(zipped.namelist()) == {"manifest.json", "launcher.cjs", "installation.json", "logo.png"}
    config = json.loads((path / "installation.json").read_text())
    assert set(config) == {"url", "executable", "installation_id", "pairing_id", "code"}
    manifest = json.loads((path / "manifest.json").read_text())
    assert "user_config" not in manifest
    assert manifest["tools_generated"] is True
    assert manifest["icon"] == "logo.png"
    assert (path / manifest["icon"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert manifest["compatibility"]["platforms"] == ["darwin"]


def test_bundle_requires_host_connector(monkeypatch):
    monkeypatch.delenv("NEUROCADE_MCP_HOST_EXECUTABLE", raising=False)
    with pytest.raises(HTTPException) as error:
        desktop_bundle({})
    assert error.value.status_code == 409


def test_launcher_rejects_missing_connector_without_logging_pairing(bundle):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the desktop launcher")
    path, _ = bundle
    result = subprocess.run([node, str(path / "launcher.cjs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "connector is missing" in result.stderr
    assert result.stdout == ""
    assert "ncpair_do_not_log" not in result.stderr
