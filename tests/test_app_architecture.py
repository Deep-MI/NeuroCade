"""Test app architecture behavior for NeuroCade."""

from pathlib import Path
import sys
import tomllib
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api-service"))

from backend_common import providers as provider_module
from backend_common.providers import ProviderRole, provider_registry
from backend_common.scan import classify_volume_metadata
from backend_common.settings import Settings, get_settings


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


def test_compose_stack_exposes_clerk_audience_to_backend_services():
    compose_text = Path("compose.yaml").read_text()
    assert "CLERK_AUDIENCE" in compose_text
    assert "VITE_CLERK_JWT_TEMPLATE" in compose_text


def test_compose_stack_uses_container_database_url():
    compose_text = Path("compose.yaml").read_text()

    assert "NEUROCADE_CONTAINER_DATABASE_URL" in compose_text
    assert "DATABASE_URL: ${DATABASE_URL" not in compose_text
    assert "sqlite+pysqlite:////data/neurocade.db" in compose_text


def test_compose_stack_pins_project_python_version():
    backend_dockerfile = Path("docker/backend.Dockerfile").read_text()
    installer_text = Path("scripts/install/python.sh").read_text()
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == ">=3.12,<3.13"
    assert pyproject["tool"]["uv"]["package"] is False
    assert pyproject["tool"]["pyright"]["pythonVersion"] == "3.12"
    assert not Path("pyrightconfig.json").exists()
    assert "NEUROCADE_PYTHON_VERSION" not in installer_text
    assert "ensure_uv_state_dir" in installer_text
    assert "XDG_CONFIG_HOME" in installer_text
    assert "UV_CACHE_DIR" in installer_text
    assert "UV_INSTALL_DIR" in installer_text
    assert "INSTALLER_NO_MODIFY_PATH=1" in installer_text
    assert ".runtime/uv/bin" in installer_text
    assert "python:3.12-slim" in backend_dockerfile
    assert "uv pip install" in backend_dockerfile


def test_pyproject_is_python_dependency_source_of_truth():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    dependencies = set(pyproject["project"]["dependencies"])
    test_dependencies = set(pyproject["project"]["optional-dependencies"]["test"])

    assert not Path("api-service/requirements.txt").exists()
    assert not Path("tests/requirements.txt").exists()
    assert "fastapi==0.128.8" in dependencies
    assert "neurocade-runtime-tools" in dependencies
    assert "pytest>=8.0" in test_dependencies
    assert pyproject["tool"]["uv"]["sources"]["neurocade-runtime-tools"] == {
        "path": "packages/neurocade-runtime-tools",
        "editable": True,
    }


def test_compose_stack_does_not_inject_dead_proxy_url_into_backend_services():
    compose_text = Path("compose.yaml").read_text()
    assert "LLM_PROXY_URL" not in compose_text


def test_compose_stack_uses_single_data_root_variable():
    compose_text = Path("compose.yaml").read_text()
    assert "HOST_DATA_DIR" in compose_text
    assert "NEUROCADE_DATA_ROOT" not in compose_text
    assert "FASTSURFER_DATA_ROOT" not in compose_text


def test_env_example_documents_clerk_audience():
    env_example = Path(".env.example").read_text()
    assert "CLERK_AUDIENCE=" in env_example
    assert "VITE_CLERK_JWT_TEMPLATE=" in env_example


def test_env_example_documents_monitoring_admin_allowlist():
    env_example = Path(".env.example").read_text()
    assert "ASSISTANT_MAX_ROUNDS=18" in env_example
    assert "MONITORING_ADMIN_USER_IDS=" in env_example
    assert "MONITORING_ACTIVE_WINDOW_MINUTES=" in env_example


def test_spa_mount_serves_vite_public_files(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend_common.settings as settings_module
    from api_service.main import _mount_client

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
