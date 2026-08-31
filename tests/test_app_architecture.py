"""Test app architecture behavior for NeuroCade."""

import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api-service"))

from backend_common import providers as provider_module
from backend_common.db import _build_engine
from backend_common.providers import provider_registry
from backend_common.settings import Settings


def _settings_without_env_file() -> Settings:
    """Build settings without loading repository environment files."""
    return cast(Any, Settings)(_env_file=None)


def test_browser_startup_message_highlights_url_and_honors_no_color(monkeypatch):
    from api_service.main import browser_startup_message

    url = "http://127.0.0.1:8000"
    monkeypatch.delenv("NO_COLOR", raising=False)
    colored = browser_startup_message(url)
    assert "\033[1;32m" in colored
    assert f"\033[1;36m{url}\033[0m" in colored

    monkeypatch.setenv("NO_COLOR", "1")
    plain = browser_startup_message(url)
    assert "\033[" not in plain
    assert plain == "Open NeuroCade in a browser at http://127.0.0.1:8000"


def test_provider_registry_exposes_chat_models():
    assert {model.provider for model in provider_registry.list_models()} >= {"openai-compatible", "no-llm"}


def test_openai_compatible_provider_builds_langchain_model(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "backend-token")
    registry = provider_module.ProviderRegistry()

    cfg = registry.get()
    assert cfg.provider == "openai-compatible"
    assert cfg.provider_family == "openai_compatible"
    assert cfg.base_url == provider_module.settings.llm_backend_url

    model = registry.build_chat_model()
    assert type(model).__name__ == "ChatOpenAI"


def test_openai_compatible_provider_allows_blank_api_key(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "")

    registry = provider_module.ProviderRegistry()
    cfg = registry.get()

    assert cfg.configured is True
    assert cfg.api_key is None


def test_openai_compatible_provider_disables_qwen_thinking_by_default(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_disable_thinking", True)
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "backend-token")
    registry = provider_module.ProviderRegistry()

    model = registry.build_chat_model()

    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_no_llm_provider_is_registered_but_unconfigured(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "no-llm")
    registry = provider_module.ProviderRegistry()

    cfg = registry.get()

    assert cfg.provider == "no-llm"
    assert cfg.provider_family == "none"
    assert cfg.configured is False
    assert cfg.configuration_reason == "LLM setup was skipped during install"
    assert registry.default_provider == "no-llm"


def test_no_llm_provider_does_not_build_chat_model(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "no-llm")
    registry = provider_module.ProviderRegistry()

    with pytest.raises(ValueError, match="LLM setup was skipped during install"):
        registry.build_chat_model()


def test_provider_reachability_is_separate_from_configuration(monkeypatch):
    config = provider_module.ModelConfig(
        provider="test-provider",
        provider_family="openai_compatible",
        model="model",
        base_url="https://provider-status.example.invalid",
        configured=True,
    )
    provider_module._probe_provider.cache_clear()
    monkeypatch.setattr(
        provider_module.requests,
        "get",
        lambda *_args, **_kwargs: type("Response", (), {"ok": True, "json": lambda self: {"data": [{"id": "model"}]}})(),
    )

    reachable, reason = provider_module.provider_reachability(config)

    assert reachable is True
    assert reason is None


def test_provider_reachability_rejects_unlisted_openai_model(monkeypatch):
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
            {"ok": True, "json": lambda self: {"data": [{"id": "available-model"}]}}
        )(),
    )

    reachable, reason = provider_module.provider_reachability(config)

    assert reachable is False
    assert reason == "Configured model 'removed-model' is not listed by the provider"


def test_settings_default_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = _settings_without_env_file()
    assert settings.sqlalchemy_database_url.startswith("sqlite+pysqlite:///")
    assert settings.sqlalchemy_database_url.endswith("neurocade.db")


def test_database_engine_rejects_non_sqlite_urls():
    with pytest.raises(RuntimeError, match="supports SQLite DATABASE_URL values only"):
        _build_engine("postgresql://example.invalid/neurocade")


def test_assistant_max_rounds_default_is_shared_limit():
    settings = _settings_without_env_file()

    assert settings.assistant_max_rounds == 18


def test_settings_derives_outputs_from_host_data_dir(monkeypatch, tmp_path):
    data_root = tmp_path / "neurocade-data"
    monkeypatch.setenv("HOST_DATA_DIR", str(data_root))

    settings = _settings_without_env_file()

    assert settings.fs_data_root == data_root
    assert settings.outputs_dir == data_root / "output"


def test_settings_ignore_removed_data_root_aliases(monkeypatch, tmp_path):
    monkeypatch.delenv("HOST_DATA_DIR", raising=False)
    monkeypatch.setenv("NEUROCADE_DATA_ROOT", str(tmp_path / "ignored-data"))
    monkeypatch.setenv("NEUROCADE_OUTPUTS_DIR", str(tmp_path / "ignored-output"))

    settings = _settings_without_env_file()

    assert settings.fs_data_root != tmp_path / "ignored-data"
    assert settings.outputs_dir != tmp_path / "ignored-output"


def test_docker_launcher_exposes_clerk_environment():
    driver_text = Path("scripts/lib/runtime_docker.sh").read_text()
    assert "--env-file" in driver_text


def test_docker_launcher_uses_mounted_sqlite_database():
    driver_text = Path("scripts/lib/runtime_docker.sh").read_text()
    launcher_text = Path("scripts/run.sh").read_text()

    assert "sqlite+pysqlite:////database/neurocade.db" in driver_text
    assert '"$DATABASE_VOLUME:/database"' in driver_text
    assert 'docker volume create "$DATABASE_VOLUME"' in driver_text
    assert "runtime_prepare_database" in launcher_text
    assert '"$HOST_DATA_DIR:/database"' not in driver_text
    assert "NEUROCADE_CONTAINER_DATABASE_URL" not in driver_text


def test_gui_command_channel_polls_immediately_and_continuously():
    sync_hook = Path("client/src/hooks/useGuiStateSync.ts").read_text()

    assert "const GUI_STATE_SYNC_INTERVAL_MS = 2000" in sync_hook
    assert sync_hook.index("syncState()") < sync_hook.index(
        "window.setInterval(syncState, GUI_STATE_SYNC_INTERVAL_MS)"
    )
    assert "lastSyncedSignatureRef" not in sync_hook
    assert "if (syncInFlight) return" in sync_hook


def test_docker_image_pins_project_python_version():
    backend_dockerfile = Path("docker/backend.Dockerfile").read_text()
    build_script = Path("scripts/build_image.sh").read_text()
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == ">=3.12,<3.13"
    assert pyproject["tool"]["uv"]["package"] is False
    assert pyproject["tool"]["ruff"]["target-version"] == "py312"
    assert pyproject["tool"]["ruff"]["lint"]["select"] == ["E", "F", "I", "UP", "B", "SIM"]
    assert pyproject["tool"]["pyright"]["pythonVersion"] == "3.12"
    assert not Path("pyrightconfig.json").exists()
    assert "npm ci" not in build_script
    assert "python:3.12-slim" in backend_dockerfile
    assert "SQLITE_AUTOCONF_VERSION=3530400" in backend_dockerfile
    assert "sqlite3.sqlite_version" in backend_dockerfile
    assert "uv sync --locked --no-dev --no-editable" in backend_dockerfile
    assert "pip uninstall -y uv" in backend_dockerfile
    assert "node:22-alpine" in backend_dockerfile
    assert Path("uv.lock").exists()


def test_pyproject_is_python_dependency_source_of_truth():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    dependencies = set(pyproject["project"]["dependencies"])
    test_dependencies = set(pyproject["project"]["optional-dependencies"]["test"])

    assert not Path("api-service/requirements.txt").exists()
    assert not Path("tests/requirements.txt").exists()
    assert "fastapi==0.139.2" in dependencies
    assert "starlette==1.3.1" in dependencies
    assert "python-multipart==0.0.32" in dependencies
    assert "neurocade-runtime-tools" in dependencies
    assert "pytest>=8.0" in test_dependencies
    assert "ruff==0.16.0" in test_dependencies
    assert pyproject["tool"]["uv"]["sources"]["neurocade-runtime-tools"] == {
        "path": "packages/neurocade-runtime-tools",
        "editable": True,
    }


def test_docker_launcher_does_not_inject_dead_proxy_url():
    run_text = Path("scripts/run.sh").read_text()
    assert "LLM_PROXY_URL" not in run_text


def test_docker_launcher_uses_single_data_root_variable():
    run_text = Path("scripts/run.sh").read_text()
    assert "HOST_DATA_DIR" in run_text
    assert "NEUROCADE_DATA_ROOT" not in run_text
    assert "FASTSURFER_DATA_ROOT" not in run_text


def test_env_example_documents_clerk_audience():
    env_example = Path(".env.example").read_text()
    assert "CLERK_AUDIENCE=" in env_example
    assert "CLERK_PUBLISHABLE_KEY=" in env_example
    assert "CLERK_JWT_TEMPLATE=" in env_example
    assert "VITE_CLERK" not in env_example


def test_env_example_documents_monitoring_admin_allowlist():
    env_example = Path(".env.example").read_text()
    assert "ASSISTANT_MAX_ROUNDS=18" in env_example
    assert "ASSISTANT_WORKFLOW_WAIT_SECONDS=300" in env_example
    assert "ASSISTANT_GUI_ACK_WAIT_SECONDS=10" in env_example
    assert "MONITORING_ADMIN_USER_IDS=" in env_example
    assert "MONITORING_ACTIVE_WINDOW_MINUTES=" in env_example


def test_env_example_uses_canonical_url_and_model_settings():
    env_example = Path(".env.example").read_text()
    assert "APP_BASE_URL=" in env_example
    assert "LLM_BACKEND_MODEL=" in env_example
    assert "APP_PUBLIC_URL" not in env_example
    assert "LLM_CHAT_MODEL" not in env_example


def test_spa_mount_serves_vite_public_files(monkeypatch, tmp_path):
    from api_service.main import _mount_client
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend_common.settings as settings_module

    dist_dir = tmp_path / "client" / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist_dir / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets_dir / "index-abc123.js").write_text("export {};", encoding="utf-8")
    monkeypatch.setattr(settings_module, "ROOT_DIR", tmp_path)

    application = FastAPI()
    _mount_client(application)
    client = TestClient(application)

    public_response = client.get("/favicon.svg?v=4")
    assert public_response.status_code == 200
    assert public_response.text == "<svg></svg>"
    assert public_response.headers["cache-control"] == "no-cache"
    spa_response = client.get("/workspace/demo")
    assert spa_response.text == "<html>spa</html>"
    assert spa_response.headers["cache-control"] == "no-cache"
    assert client.get("/assets/index-abc123.js").headers["cache-control"] == "public, max-age=31536000, immutable"
