"""High-value application architecture contracts."""

import sys
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api-service"))

from backend_common import providers as provider_module
from backend_common.db import _build_engine
from backend_common.settings import Settings


def _settings_without_env_file() -> Settings:
    return cast(Any, Settings)(_env_file=None)


def test_configured_provider_builds_chat_model(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "backend-token")
    monkeypatch.setattr(provider_module.settings, "llm_disable_thinking", True)

    registry = provider_module.ProviderRegistry()
    model = registry.build_chat_model()

    assert type(model).__name__ == "ChatOpenAI"
    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_no_llm_provider_is_unconfigured_and_cannot_build(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "no-llm")
    registry = provider_module.ProviderRegistry()

    assert registry.get().configured is False
    with pytest.raises(ValueError, match="LLM setup was skipped during install"):
        registry.build_chat_model()


def test_provider_reachability_rejects_missing_model(monkeypatch):
    config = provider_module.ModelConfig(
        provider="test-provider",
        provider_family="openai_compatible",
        model="removed-model",
        base_url="https://provider-status.example.invalid",
        configured=True,
    )
    provider_module._probe_provider.cache_clear()
    monkeypatch.setattr(
        provider_module.requests,
        "get",
        lambda *_args, **_kwargs: type(
            "Response",
            (),
            {"ok": True, "json": lambda self: {"data": [{"id": "available-model"}]}},
        )(),
    )

    assert provider_module.provider_reachability(config) == (
        False,
        "Configured model 'removed-model' is not listed by the provider",
    )


def test_settings_enforce_sqlite_and_derive_output_path(monkeypatch, tmp_path):
    data_root = tmp_path / "neurocade-data"
    monkeypatch.setenv("HOST_DATA_DIR", str(data_root))
    settings = _settings_without_env_file()

    assert settings.outputs_dir == data_root / "output"
    with pytest.raises(RuntimeError, match="supports SQLite DATABASE_URL values only"):
        _build_engine("postgresql://example.invalid/neurocade")


def test_spa_mount_serves_public_files_with_expected_caching(monkeypatch, tmp_path):
    from api_service.main import _mount_client
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend_common.settings as settings_module

    dist_dir = tmp_path / "client" / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist_dir / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets_dir / "index.js").write_text("export {};", encoding="utf-8")
    monkeypatch.setattr(settings_module, "ROOT_DIR", tmp_path)

    application = FastAPI()
    _mount_client(application)
    client = TestClient(application)

    assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"
    assert client.get("/workspace/demo").text == "<html>spa</html>"
    assert client.get("/assets/index.js").headers["cache-control"] == "public, max-age=31536000, immutable"
