"""Test app architecture behavior for NeuroCade."""

import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api-service"))

from backend_common import providers as provider_module
from backend_common.providers import ProviderRole, provider_registry
from backend_common.scan import classify_volume_metadata
from backend_common.settings import Settings


def _settings_without_env_file() -> Settings:
    """Build settings without loading repository environment files."""
    return cast(Any, Settings)(_env_file=None)


def test_provider_registry_exposes_chat_and_orchestration_models():
    models = provider_registry.list_models()
    roles = {model.role for model in models}
    assert ProviderRole.chat in roles
    assert ProviderRole.orchestration in roles


def test_openai_compatible_provider_builds_langchain_model(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "workflow_default_provider", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "backend-token")
    registry = provider_module.ProviderRegistry()

    cfg = registry.get(ProviderRole.chat)
    assert cfg.provider == "openai-compatible"
    assert cfg.provider_family == "openai_compatible"
    assert cfg.base_url == provider_module.settings.llm_backend_url

    model = registry.build_chat_model(ProviderRole.chat)
    assert type(model).__name__ == "ChatOpenAI"


def test_openai_compatible_provider_allows_blank_api_key(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "openai-compatible")
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "")

    registry = provider_module.ProviderRegistry()
    cfg = registry.get(ProviderRole.chat)

    assert cfg.available is True
    assert cfg.api_key is None


def test_openai_compatible_provider_disables_qwen_thinking_by_default(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_backend_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(provider_module.settings, "llm_disable_thinking", True)
    monkeypatch.setattr(provider_module.settings, "llm_backend_api_key", "backend-token")
    registry = provider_module.ProviderRegistry()

    model = registry.build_chat_model(ProviderRole.chat)

    assert model.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_no_llm_provider_is_registered_but_unavailable(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "no-llm")
    monkeypatch.setattr(provider_module.settings, "workflow_default_provider", "no-llm")
    registry = provider_module.ProviderRegistry()

    cfg = registry.get(ProviderRole.chat)

    assert cfg.provider == "no-llm"
    assert cfg.provider_family == "none"
    assert cfg.available is False
    assert cfg.availability_reason == "LLM setup was skipped during install"
    assert registry.default_provider_for_role(ProviderRole.chat) == "no-llm"


def test_no_llm_provider_does_not_build_chat_model(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "llm_provider_default", "no-llm")
    registry = provider_module.ProviderRegistry()

    with pytest.raises(ValueError, match="LLM setup was skipped during install"):
        registry.build_chat_model(ProviderRole.chat)


def test_settings_default_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = _settings_without_env_file()
    assert settings.sqlalchemy_database_url.startswith("sqlite+pysqlite:///")
    assert settings.sqlalchemy_database_url.endswith("neurocade.db")


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


def test_classify_volume_metadata_distinguishes_segmentation_and_intensity():
    assert classify_volume_metadata("orig.mgz")["volume_role"] == "intensity"
    aseg = classify_volume_metadata("aparc.DKTatlas+aseg.deep.mgz")
    assert aseg["volume_role"] == "segmentation"
    assert aseg["lut"] == "freesurfer"
    mask = classify_volume_metadata("brainmask_bin.nii.gz")
    assert mask["volume_role"] == "segmentation"
    assert mask["lut"] == "binary"


def test_docker_launcher_exposes_clerk_environment():
    run_text = Path("scripts/run.sh").read_text()
    assert "--env-file" in run_text


def test_docker_launcher_uses_mounted_sqlite_database():
    run_text = Path("scripts/run.sh").read_text()

    assert "sqlite+pysqlite:////database/neurocade.db" in run_text
    assert '"${NEUROCADE_DB_DIR}:/database"' in run_text
    assert "NEUROCADE_CONTAINER_DATABASE_URL" not in run_text


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
    assert "MONITORING_ADMIN_USER_IDS=" in env_example
    assert "MONITORING_ACTIVE_WINDOW_MINUTES=" in env_example


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
    monkeypatch.setattr(settings_module, "ROOT_DIR", tmp_path)

    application = FastAPI()
    _mount_client(application)
    client = TestClient(application)

    public_response = client.get("/favicon.svg?v=4")
    assert public_response.status_code == 200
    assert public_response.text == "<svg></svg>"
    assert client.get("/workspace/demo").text == "<html>spa</html>"
