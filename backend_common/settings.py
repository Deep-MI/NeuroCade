"""Provide shared backend settings utilities for NeuroCade."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    deployment_profile: str = Field(default="local", alias="DEPLOYMENT_PROFILE")
    app_allowed_hosts: str = Field(default="", alias="APP_ALLOWED_HOSTS")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    fs_data_root: Path = Field(default=ROOT_DIR / "neurocade-data", alias="HOST_DATA_DIR")
    sif_dir: Path = Field(default=ROOT_DIR / "neurocade-data" / "sif", alias="NEUROCADE_SIF_DIR")
    dicom_conversion_timeout_seconds: int = Field(default=300, alias="DICOM_CONVERSION_TIMEOUT_SECONDS")
    max_upload_file_size_bytes: int = Field(default=2 * 1024 * 1024 * 1024, alias="MAX_UPLOAD_FILE_SIZE_BYTES")
    dicom_zip_max_entries: int = Field(default=5000, alias="DICOM_ZIP_MAX_ENTRIES")
    dicom_zip_max_expanded_bytes: int = Field(default=4 * 1024 * 1024 * 1024, alias="DICOM_ZIP_MAX_EXPANDED_BYTES")

    clerk_publishable_key: str | None = Field(default=None, alias="CLERK_PUBLISHABLE_KEY")
    clerk_jwt_template: str | None = Field(default=None, alias="CLERK_JWT_TEMPLATE")
    clerk_secret_key: str | None = Field(default=None, alias="CLERK_SECRET_KEY")
    clerk_jwks_url: str | None = Field(default=None, alias="CLERK_JWKS_URL")
    clerk_issuer: str | None = Field(default=None, alias="CLERK_ISSUER")
    clerk_audience: str | None = Field(default=None, alias="CLERK_AUDIENCE")
    local_auth_enabled: bool = Field(default=False, alias="LOCAL_AUTH_ENABLED")
    local_auth_user_id: str = Field(default="local-user", alias="LOCAL_AUTH_USER_ID")
    local_auth_email: str = Field(default="local@example.com", alias="LOCAL_AUTH_EMAIL")
    local_auth_name: str = Field(default="Local User", alias="LOCAL_AUTH_NAME")
    chat_rate_limit_window_seconds: int = Field(default=60, alias="CHAT_RATE_LIMIT_WINDOW_SECONDS")
    chat_max_requests_per_window: int = Field(default=30, alias="CHAT_MAX_REQUESTS_PER_WINDOW")
    chat_max_concurrent_requests: int = Field(default=8, alias="CHAT_MAX_CONCURRENT_REQUESTS")
    chat_max_concurrent_per_key: int = Field(default=2, alias="CHAT_MAX_CONCURRENT_PER_KEY")
    job_history_retention_days: int = Field(default=30, alias="JOB_HISTORY_RETENTION_DAYS")
    assistant_turn_timeout_seconds: int = Field(default=600, alias="ASSISTANT_TURN_TIMEOUT_SECONDS")
    assistant_workflow_wait_seconds: float = Field(default=300, gt=0, alias="ASSISTANT_WORKFLOW_WAIT_SECONDS")
    assistant_gui_ack_wait_seconds: float = Field(default=10, gt=0, alias="ASSISTANT_GUI_ACK_WAIT_SECONDS")
    assistant_max_rounds: int = Field(default=18, alias="ASSISTANT_MAX_ROUNDS")
    assistant_history_max_messages: int = Field(default=40, ge=1, alias="ASSISTANT_HISTORY_MAX_MESSAGES")
    assistant_history_max_characters: int = Field(default=120_000, ge=1000, alias="ASSISTANT_HISTORY_MAX_CHARACTERS")
    assistant_history_max_tokens: int = Field(default=30_000, ge=256, alias="ASSISTANT_HISTORY_MAX_TOKENS")
    assistant_history_keep_recent_tokens: int = Field(default=12_000, ge=128, alias="ASSISTANT_HISTORY_KEEP_RECENT_TOKENS")
    assistant_prompt_max_characters: int = Field(default=120_000, ge=20_000, alias="ASSISTANT_PROMPT_MAX_CHARACTERS")
    assistant_prompt_max_tokens: int = Field(default=30_000, ge=256, alias="ASSISTANT_PROMPT_MAX_TOKENS")
    assistant_history_display_limit: int = Field(default=200, alias="ASSISTANT_HISTORY_DISPLAY_LIMIT")
    monitoring_admin_user_ids: str = Field(default="", alias="MONITORING_ADMIN_USER_IDS")
    monitoring_active_window_minutes: int = Field(default=15, alias="MONITORING_ACTIVE_WINDOW_MINUTES")
    monitoring_event_retention_days: int = Field(default=30, alias="MONITORING_EVENT_RETENTION_DAYS")
    llm_backend_url: str = Field(default="", alias="LLM_BACKEND_URL")
    llm_backend_api_key: str | None = Field(default="", alias="LLM_BACKEND_API_KEY")
    llm_backend_model: str = Field(default="Qwen/Qwen3.6-35B-A3B", alias="LLM_BACKEND_MODEL")
    llm_provider_default: str = Field(default="openai-compatible", alias="LLM_PROVIDER_DEFAULT")
    llm_disable_thinking: bool = Field(default=True, alias="LLM_DISABLE_THINKING")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str | None = Field(default=None, alias="ANTHROPIC_MODEL")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    google_model: str | None = Field(default=None, alias="GOOGLE_MODEL")
    ollama_base_url: str = Field(default="http://ollama:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str | None = Field(default=None, alias="OLLAMA_MODEL")

    @property
    def outputs_dir(self) -> Path:
        """Directory for generated neuroimaging outputs."""
        return self.fs_data_root / "output"

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return the configured SQLAlchemy URL, defaulting to a local SQLite file."""
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.fs_data_root / 'neurocade.db'}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings and ensure the output directory exists."""
    settings = Settings()
    os.environ.setdefault("NEUROCADE_SIF_DIR", str(settings.sif_dir))
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    return settings
