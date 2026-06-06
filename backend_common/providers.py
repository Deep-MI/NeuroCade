"""Provide shared backend providers utilities for NeuroCade."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from backend_common.settings import get_settings


settings = get_settings()


class ProviderRole(str, Enum):
    chat = "chat"
    orchestration = "orchestration"
    embedding = "embedding"


@dataclass(frozen=True)
class ProviderCapabilities:
    native_tool_calling: bool = False
    json_mode: bool = True
    vision: bool = False
    streaming: bool = True


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    provider_family: str
    model: str
    role: ProviderRole
    base_url: str | None = None
    api_key: str | None = None
    capabilities: ProviderCapabilities = ProviderCapabilities()
    available: bool = True
    availability_reason: str | None = None


def _optional_import(module_name: str):
    """Import an optional provider package, returning None when it is unavailable."""
    try:
        module = __import__(module_name, fromlist=["*"])
    except ModuleNotFoundError:
        return None
    return module


def _normalize_provider_name(provider_name: str | None) -> str:
    """Normalize provider identifiers for case-insensitive matching."""
    return (provider_name or "").strip().lower().replace("_", "-")


def _openai_compatible_extra_body() -> dict[str, Any]:
    """Build request extras for OpenAI-compatible chat backends."""
    if not settings.llm_disable_thinking:
        return {}
    return {"chat_template_kwargs": {"enable_thinking": False}}


def _configured_secret(value: str | None) -> str | None:
    """Return a configured secret, ignoring blank and placeholder values."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped in {"EMPTY", "CHANGE_ME"}:
        return None
    return stripped


def _openai_compatible_api_key() -> str | None:
    """Read the configured API key for OpenAI-compatible backends."""
    return _configured_secret(settings.llm_backend_api_key)


def _openai_compatible_availability_reason(base_url: str | None) -> str | None:
    """Explain why an OpenAI-compatible backend cannot be used, if applicable."""
    if not base_url:
        return "LLM_BACKEND_URL is not configured"
    return None


def _no_llm_config(role: ProviderRole) -> ModelConfig:
    return ModelConfig(
        provider="no-llm",
        provider_family="none",
        model="no-llm",
        role=role,
        capabilities=ProviderCapabilities(native_tool_calling=False, json_mode=False, vision=False, streaming=False),
        available=False,
        availability_reason="LLM setup was skipped during install",
    )


class ProviderRegistry:
    def __init__(self) -> None:
        default_openai_capabilities = ProviderCapabilities(
            native_tool_calling=settings.llm_native_tool_calling,
            json_mode=settings.llm_json_mode,
            vision=False,
            streaming=True,
        )
        chat_api_key = _openai_compatible_api_key()
        chat_availability_reason = _openai_compatible_availability_reason(
            settings.llm_backend_url,
        )
        orchestration_api_key = _openai_compatible_api_key()
        orchestration_availability_reason = _openai_compatible_availability_reason(
            settings.llm_backend_url,
        )
        self._default_provider_by_role = {
            ProviderRole.chat: settings.llm_provider_default,
            ProviderRole.orchestration: settings.workflow_default_provider,
        }
        self._configs: dict[ProviderRole, list[ModelConfig]] = {
            ProviderRole.chat: [
                ModelConfig(
                    provider="openai-compatible",
                    provider_family="openai_compatible",
                    model=settings.llm_chat_model or settings.llm_backend_model,
                    role=ProviderRole.chat,
                    base_url=settings.llm_backend_url,
                    api_key=chat_api_key,
                    capabilities=default_openai_capabilities,
                    available=chat_availability_reason is None,
                    availability_reason=chat_availability_reason,
                ),
                _no_llm_config(ProviderRole.chat),
            ],
            ProviderRole.orchestration: [
                ModelConfig(
                    provider="openai-compatible",
                    provider_family="openai_compatible",
                    model=settings.llm_orchestration_model or settings.llm_backend_model,
                    role=ProviderRole.orchestration,
                    base_url=settings.llm_backend_url,
                    api_key=orchestration_api_key,
                    capabilities=default_openai_capabilities,
                    available=orchestration_availability_reason is None,
                    availability_reason=orchestration_availability_reason,
                ),
                _no_llm_config(ProviderRole.orchestration),
            ],
            ProviderRole.embedding: [],
        }
        self._register_optional_families()

    def _register_optional_families(self) -> None:
        """Register provider families backed by optional LangChain packages."""
        self._append_optional_config(
            role=ProviderRole.chat,
            config=ModelConfig(
                provider="anthropic",
                provider_family="anthropic",
                model=settings.anthropic_model or "claude-3-5-sonnet-latest",
                role=ProviderRole.chat,
                api_key=settings.anthropic_api_key,
                capabilities=ProviderCapabilities(native_tool_calling=True, json_mode=True, vision=True, streaming=True),
                available=bool(settings.anthropic_api_key),
                availability_reason=None if settings.anthropic_api_key else "ANTHROPIC_API_KEY is not configured",
            ),
            module_name="langchain_anthropic",
        )
        self._append_optional_config(
            role=ProviderRole.orchestration,
            config=ModelConfig(
                provider="anthropic",
                provider_family="anthropic",
                model=settings.anthropic_model or "claude-3-5-sonnet-latest",
                role=ProviderRole.orchestration,
                api_key=settings.anthropic_api_key,
                capabilities=ProviderCapabilities(native_tool_calling=True, json_mode=True, vision=True, streaming=True),
                available=bool(settings.anthropic_api_key),
                availability_reason=None if settings.anthropic_api_key else "ANTHROPIC_API_KEY is not configured",
            ),
            module_name="langchain_anthropic",
        )
        self._append_optional_config(
            role=ProviderRole.chat,
            config=ModelConfig(
                provider="google",
                provider_family="google",
                model=settings.google_model or "gemini-2.0-flash",
                role=ProviderRole.chat,
                api_key=settings.google_api_key,
                capabilities=ProviderCapabilities(native_tool_calling=True, json_mode=True, vision=True, streaming=True),
                available=bool(settings.google_api_key),
                availability_reason=None if settings.google_api_key else "GOOGLE_API_KEY is not configured",
            ),
            module_name="langchain_google_genai",
        )
        self._append_optional_config(
            role=ProviderRole.orchestration,
            config=ModelConfig(
                provider="google",
                provider_family="google",
                model=settings.google_model or "gemini-2.0-flash",
                role=ProviderRole.orchestration,
                api_key=settings.google_api_key,
                capabilities=ProviderCapabilities(native_tool_calling=True, json_mode=True, vision=True, streaming=True),
                available=bool(settings.google_api_key),
                availability_reason=None if settings.google_api_key else "GOOGLE_API_KEY is not configured",
            ),
            module_name="langchain_google_genai",
        )
        self._append_optional_config(
            role=ProviderRole.chat,
            config=ModelConfig(
                provider="ollama",
                provider_family="ollama",
                model=settings.ollama_model or "gemma4:e2b",
                role=ProviderRole.chat,
                base_url=settings.ollama_base_url,
                capabilities=ProviderCapabilities(native_tool_calling=False, json_mode=True, vision=False, streaming=True),
                available=bool(settings.ollama_base_url),
                availability_reason=None if settings.ollama_base_url else "OLLAMA_BASE_URL is not configured",
            ),
            module_name="langchain_ollama",
        )
        self._append_optional_config(
            role=ProviderRole.orchestration,
            config=ModelConfig(
                provider="ollama",
                provider_family="ollama",
                model=settings.ollama_model or "gemma4:e2b",
                role=ProviderRole.orchestration,
                base_url=settings.ollama_base_url,
                capabilities=ProviderCapabilities(native_tool_calling=False, json_mode=True, vision=False, streaming=True),
                available=bool(settings.ollama_base_url),
                availability_reason=None if settings.ollama_base_url else "OLLAMA_BASE_URL is not configured",
            ),
            module_name="langchain_ollama",
        )

    def _append_optional_config(self, *, role: ProviderRole, config: ModelConfig, module_name: str) -> None:
        """Add a provider config, marking it unavailable if its package is missing."""
        if _optional_import(module_name) is None:
            config = ModelConfig(
                **{**config.__dict__, "available": False, "availability_reason": f"{module_name} is not installed"},
            )
        self._configs[role].append(config)

    def list_models(self) -> list[ModelConfig]:
        """Return all registered model configurations."""
        return [config for configs in self._configs.values() for config in configs]

    def default_provider_for_role(self, role: ProviderRole) -> str | None:
        """Return the configured default provider name for a model role."""
        return self._default_provider_by_role.get(role)

    def is_default_provider(self, role: ProviderRole, provider: str) -> bool:
        """Return whether a provider matches the configured default for a role."""
        return _normalize_provider_name(provider) == _normalize_provider_name(self.default_provider_for_role(role))

    def get(self, role: ProviderRole, provider_override: str | None = None, model_override: str | None = None) -> ModelConfig:
        """Resolve the active provider configuration for a role."""
        configs = self._configs[role]
        if not configs:
            raise ValueError(f"No provider configured for role {role.value}")
        requested_provider = provider_override or self._default_provider_by_role.get(role) or configs[0].provider
        normalized_override = _normalize_provider_name(requested_provider)
        config = next(
            (candidate for candidate in configs if _normalize_provider_name(candidate.provider) == normalized_override),
            None,
        )
        if config is None:
            raise ValueError(f"Unsupported provider override: {requested_provider}")
        resolved_api_key = (
            _openai_compatible_api_key()
            if config.provider_family == "openai_compatible"
            else config.api_key
        )
        availability_reason = (
            _openai_compatible_availability_reason(config.base_url)
            if config.provider_family == "openai_compatible"
            else config.availability_reason
        )
        return ModelConfig(
            provider=config.provider,
            provider_family=config.provider_family,
            model=model_override or config.model,
            role=role,
            base_url=config.base_url,
            api_key=resolved_api_key,
            capabilities=config.capabilities,
            available=availability_reason is None,
            availability_reason=availability_reason,
        )

    def build_chat_model(self, role: ProviderRole, provider_override: str | None = None, model_override: str | None = None) -> Any:
        """Create a LangChain chat model for the resolved provider."""
        config = self.get(role, provider_override=provider_override, model_override=model_override)
        if not config.available:
            raise ValueError(config.availability_reason or f"Provider {config.provider} is not available")

        if config.provider_family == "openai_compatible":
            return ChatOpenAI(
                model=config.model,
                api_key=SecretStr(config.api_key or "not-set"),
                base_url=f"{(config.base_url or '').rstrip('/')}/v1",
                temperature=0,
                extra_body=_openai_compatible_extra_body(),
            )

        if config.provider_family == "anthropic":
            module = _optional_import("langchain_anthropic")
            if module is None:
                raise ValueError("langchain_anthropic is not installed")
            return module.ChatAnthropic(
                model=config.model,
                api_key=config.api_key or "not-set",
                temperature=0,
            )

        if config.provider_family == "google":
            module = _optional_import("langchain_google_genai")
            if module is None:
                raise ValueError("langchain_google_genai is not installed")
            return module.ChatGoogleGenerativeAI(
                model=config.model,
                google_api_key=config.api_key or "not-set",
                temperature=0,
            )

        if config.provider_family == "ollama":
            module = _optional_import("langchain_ollama")
            if module is None:
                raise ValueError("langchain_ollama is not installed")
            return module.ChatOllama(
                model=config.model,
                base_url=config.base_url,
                temperature=0,
            )

        if config.provider_family == "none":
            raise ValueError(config.availability_reason or "Assistant is disabled because no LLM provider is configured")

        raise ValueError(f"Unsupported provider family: {config.provider_family}")


provider_registry = ProviderRegistry()
