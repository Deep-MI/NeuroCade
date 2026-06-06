"""Provide shared backend settings utilities for NeuroCade."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = Field(default="NeuroCade API", validation_alias=AliasChoices("NEUROCADE_APP_NAME", "APP_NAME"))
    app_base_url: str = Field(default="http://localhost:8005", alias="APP_BASE_URL")
    deployment_profile: str = Field(default="local", alias="DEPLOYMENT_PROFILE")
    app_public_url: str | None = Field(default=None, alias="APP_PUBLIC_URL")
    app_allowed_hosts: str = Field(default="", alias="APP_ALLOWED_HOSTS")

    postgres_user: str = Field(default="fastsurfer", alias="POSTGRES_USER")
    postgres_password: str = Field(default="fastsurfer", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="fastsurfer_app", alias="POSTGRES_DB")
    postgres_host: str = Field(default="127.0.0.1", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=55432, alias="POSTGRES_PORT")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    redis_password: str = Field(default="fastsurfer-dev-redis", alias="REDIS_PASSWORD")
    redis_url: str = Field(
        default="redis://:fastsurfer-dev-redis@127.0.0.1:56379/0",
        alias="REDIS_URL",
    )

    api_service_url: str = Field(default="http://127.0.0.1:58080", alias="API_SERVICE_URL")
    host_runtime_runner_url: str | None = Field(default=None, alias="HOST_RUNTIME_RUNNER_URL")
    host_runtime_runner_token: str | None = Field(default=None, alias="HOST_RUNTIME_RUNNER_TOKEN")
    container_inventory: Path = Field(default=ROOT_DIR / "llm-data" / "tool-catalog" / "installed_containers.json", alias="NEUROCADE_CONTAINER_INVENTORY")
    installed_tools_jsonl: Path = Field(default=ROOT_DIR / "llm-data" / "tool-catalog" / "installed_tools.jsonl", alias="NEUROCADE_INSTALLED_TOOLS_JSONL")

    fs_data_root: Path = Field(default=ROOT_DIR / "neurocade-data", alias="HOST_DATA_DIR")
    outputs_dir_override: Path | None = Field(default=None, exclude=True)
    dicom_raw_retention: str = Field(default="discard", alias="DICOM_RAW_RETENTION")
    dicom_conversion_timeout_seconds: int = Field(default=300, alias="DICOM_CONVERSION_TIMEOUT_SECONDS")
    max_upload_file_size_bytes: int = Field(default=2 * 1024 * 1024 * 1024, alias="MAX_UPLOAD_FILE_SIZE_BYTES")
    dicom_zip_max_entries: int = Field(default=5000, alias="DICOM_ZIP_MAX_ENTRIES")
    dicom_zip_max_expanded_bytes: int = Field(default=4 * 1024 * 1024 * 1024, alias="DICOM_ZIP_MAX_EXPANDED_BYTES")

    clerk_publishable_key: str | None = Field(default=None, alias="VITE_CLERK_PUBLISHABLE_KEY")
    clerk_secret_key: str | None = Field(default=None, alias="CLERK_SECRET_KEY")
    clerk_jwks_url: str | None = Field(default=None, alias="CLERK_JWKS_URL")
    clerk_issuer: str | None = Field(default=None, alias="CLERK_ISSUER")
    clerk_audience: str | None = Field(default=None, alias="CLERK_AUDIENCE")
    local_auth_enabled: bool = Field(default=False, alias="LOCAL_AUTH_ENABLED")
    local_auth_user_id: str = Field(default="local-user", alias="LOCAL_AUTH_USER_ID")
    local_auth_email: str = Field(default="local@example.com", alias="LOCAL_AUTH_EMAIL")
    local_auth_name: str = Field(default="Local User", alias="LOCAL_AUTH_NAME")
    llm_api_token: str | None = Field(default=None, alias="LLM_API_TOKEN")
    chat_rate_limit_window_seconds: int = Field(default=60, alias="CHAT_RATE_LIMIT_WINDOW_SECONDS")
    chat_max_requests_per_window: int = Field(default=30, alias="CHAT_MAX_REQUESTS_PER_WINDOW")
    chat_max_concurrent_requests: int = Field(default=8, alias="CHAT_MAX_CONCURRENT_REQUESTS")
    chat_max_concurrent_per_key: int = Field(default=2, alias="CHAT_MAX_CONCURRENT_PER_KEY")
    assistant_turn_timeout_seconds: int = Field(default=600, alias="ASSISTANT_TURN_TIMEOUT_SECONDS")
    assistant_max_rounds: int = Field(default=18, alias="ASSISTANT_MAX_ROUNDS")
    monitoring_admin_user_ids: str = Field(default="", alias="MONITORING_ADMIN_USER_IDS")
    monitoring_active_window_minutes: int = Field(default=15, alias="MONITORING_ACTIVE_WINDOW_MINUTES")
    monitoring_event_retention_days: int = Field(default=30, alias="MONITORING_EVENT_RETENTION_DAYS")
    llm_backend_url: str = Field(default="", alias="LLM_BACKEND_URL")
    llm_backend_api_key: str | None = Field(default="", alias="LLM_BACKEND_API_KEY")
    llm_backend_model: str = Field(default="Qwen/Qwen3.6-35B-A3B", alias="LLM_BACKEND_MODEL")
    llm_provider_default: str = Field(default="openai-compatible", alias="LLM_PROVIDER_DEFAULT")
    llm_chat_model: str | None = Field(default=None, alias="LLM_CHAT_MODEL")
    llm_orchestration_model: str | None = Field(default=None, alias="LLM_ORCHESTRATION_MODEL")
    llm_embeddings_model: str | None = Field(default=None, alias="LLM_EMBEDDINGS_MODEL")
    llm_native_tool_calling: bool = Field(default=False, alias="LLM_NATIVE_TOOL_CALLING")
    llm_json_mode: bool = Field(default=True, alias="LLM_JSON_MODE")
    llm_disable_thinking: bool = Field(default=True, alias="LLM_DISABLE_THINKING")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str | None = Field(default=None, alias="ANTHROPIC_MODEL")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    google_model: str | None = Field(default=None, alias="GOOGLE_MODEL")
    ollama_base_url: str = Field(default="http://ollama:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str | None = Field(default=None, alias="OLLAMA_MODEL")

    workflow_default_provider: str = Field(default="openai-compatible", alias="WORKFLOW_DEFAULT_PROVIDER")

    @property
    def outputs_dir(self) -> Path:
        """Directory for generated neuroimaging outputs."""
        return self.outputs_dir_override or self.fs_data_root / "output"

    @outputs_dir.setter
    def outputs_dir(self, value: Path | str) -> None:
        """Override the generated outputs directory."""
        object.__setattr__(self, "outputs_dir_override", Path(value))

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return the configured SQLAlchemy URL or build one from Postgres settings."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings and ensure the output directory exists."""
    settings = Settings()
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    return settings
