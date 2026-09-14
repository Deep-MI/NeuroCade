"""Persistent pairing survives launches without accepting another installation."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))
sys.path.insert(0, str(ROOT / "packages/neurocade-mcp/src"))

from api_service.mcp_adapter.identity import installation_id
from neurocade_mcp import load_connection, proxy_server, validate_identity


def test_identity_persists_and_distinguishes_installations(tmp_path):
    first = installation_id(tmp_path / "first")
    assert installation_id(tmp_path / "first") == first
    assert installation_id(tmp_path / "second") != first
    assert (tmp_path / "first/.mcp-installation-id").stat().st_mode & 0o077 == 0


def test_concurrent_identity_creation(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: installation_id(tmp_path), range(24)))
    assert len(set(ids)) == 1


def test_malformed_identity_is_not_replaced(tmp_path):
    path = tmp_path / ".mcp-installation-id"
    path.write_text("broken")
    with pytest.raises(ValueError):
        installation_id(tmp_path)
    assert path.read_text() == "broken"


def test_pairing_survives_restart_but_rejects_different_identity():
    connection = {"installation_id": "installed", "client_id": "client", "launch_id": "old"}
    context = SimpleNamespace(isError=False, structuredContent={**connection, "launch_id": "new"})
    assert validate_identity(connection, context)["launch_id"] == "new"
    context.structuredContent["installation_id"] = "other"
    with pytest.raises(ValueError):
        validate_identity(connection, context)
    context.structuredContent = {**connection, "client_id": "other"}
    with pytest.raises(ValueError):
        validate_identity(connection, context)


def test_legacy_file_requires_explicit_pairing(tmp_path):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"url": "http://localhost:8000/mcp", "launch_id": "old"}))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="Legacy connection"):
        load_connection(path)


def test_proxy_preserves_initialization_instructions():
    expected = "Inspect docs, obtain approval, and retain the same idempotency key."
    server = proxy_server(SimpleNamespace(instructions=expected))
    assert server.create_initialization_options().instructions == expected
