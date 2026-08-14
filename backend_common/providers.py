"""Chat-model provider configuration and construction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from backend_common.settings import get_settings

settings = get_settings()


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    provider_family: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    vision: bool = False
    native_tool_calling: bool = True
    available: bool = True
    availability_reason: str | None = None


def _optional_module(name: str) -> Any | None:
    try:
        return import_module(name)
    except ModuleNotFoundError:
        return None


def _configured_secret(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if value and value not in {"EMPTY", "CHANGE_ME"} else None


def _openai_availability(base_url: str | None) -> str | None:
    return None if base_url else "LLM_BACKEND_URL is not configured"


class ProviderRegistry:
    def __init__(self) -> None:
        api_key = _configured_secret(settings.llm_backend_api_key)
        self.default_provider = settings.llm_provider_default
        self._configs = [
            ModelConfig(
                provider="openai-compatible",
                provider_family="openai_compatible",
                model=settings.llm_chat_model or settings.llm_backend_model,
                base_url=settings.llm_backend_url,
                api_key=api_key,
                vision=True,
                availability_reason=_openai_availability(settings.llm_backend_url),
                available=bool(settings.llm_backend_url),
            ),
            ModelConfig(
                provider="no-llm",
                provider_family="none",
                model="no-llm",
                available=False,
                availability_reason="LLM setup was skipped during install",
            ),
        ]
        self._add_optional(
            ModelConfig(
                provider="anthropic",
                provider_family="anthropic",
                model=settings.anthropic_model or "claude-3-5-sonnet-latest",
                api_key=settings.anthropic_api_key,
                vision=True,
                available=bool(settings.anthropic_api_key),
                availability_reason=None if settings.anthropic_api_key else "ANTHROPIC_API_KEY is not configured",
            ),
            "langchain_anthropic",
        )
        self._add_optional(
            ModelConfig(
                provider="google",
                provider_family="google",
                model=settings.google_model or "gemini-2.0-flash",
                api_key=settings.google_api_key,
                vision=True,
                available=bool(settings.google_api_key),
                availability_reason=None if settings.google_api_key else "GOOGLE_API_KEY is not configured",
            ),
            "langchain_google_genai",
        )
        self._add_optional(
            ModelConfig(
                provider="ollama",
                provider_family="ollama",
                model=settings.ollama_model or "gemma4:e2b",
                base_url=settings.ollama_base_url,
                available=bool(settings.ollama_base_url),
                availability_reason=None if settings.ollama_base_url else "OLLAMA_BASE_URL is not configured",
            ),
            "langchain_ollama",
        )

    def _add_optional(self, config: ModelConfig, module_name: str) -> None:
        if _optional_module(module_name) is None:
            config = replace(config, available=False, availability_reason=f"{module_name} is not installed")
        self._configs.append(config)

    def list_models(self) -> list[ModelConfig]:
        return list(self._configs)

    def is_default_provider(self, provider: str) -> bool:
        return provider.strip().lower() == self.default_provider.strip().lower()

    def get(self, provider_override: str | None = None, model_override: str | None = None) -> ModelConfig:
        provider = (provider_override or self.default_provider).strip().lower()
        config = next((item for item in self._configs if item.provider.lower() == provider), None)
        if config is None:
            raise ValueError(f"Unsupported provider override: {provider}")
        if config.provider_family == "openai_compatible":
            config = replace(
                config,
                api_key=_configured_secret(settings.llm_backend_api_key),
                available=bool(config.base_url),
                availability_reason=_openai_availability(config.base_url),
            )
        tool_call_mode = settings.llm_tool_call_mode.strip().lower()
        if tool_call_mode not in {"auto", "native", "json"}:
            raise ValueError("LLM_TOOL_CALL_MODE must be one of: auto, native, json")
        return replace(
            config,
            model=model_override or config.model,
            native_tool_calling=config.native_tool_calling and tool_call_mode != "json",
        )

    def build_chat_model(self, provider_override: str | None = None, model_override: str | None = None) -> Any:
        config = self.get(provider_override, model_override)
        if not config.available:
            raise ValueError(config.availability_reason or f"Provider {config.provider} is not available")
        if config.provider_family == "openai_compatible":
            extra_body = {} if not settings.llm_disable_thinking else {"chat_template_kwargs": {"enable_thinking": False}}
            return ChatOpenAI(
                model=config.model,
                api_key=SecretStr(config.api_key or "not-set"),
                base_url=f"{(config.base_url or '').rstrip('/')}/v1",
                temperature=0,
                extra_body=extra_body,
            )
        module_name, class_name, kwargs = {
            "anthropic": ("langchain_anthropic", "ChatAnthropic", {"api_key": config.api_key or "not-set"}),
            "google": ("langchain_google_genai", "ChatGoogleGenerativeAI", {"google_api_key": config.api_key or "not-set"}),
            "ollama": ("langchain_ollama", "ChatOllama", {"base_url": config.base_url}),
        }.get(config.provider_family, ("", "", {}))
        module = _optional_module(module_name) if module_name else None
        if module is None:
            raise ValueError(config.availability_reason or f"Unsupported provider family: {config.provider_family}")
        return getattr(module, class_name)(model=config.model, temperature=0, **kwargs)


provider_registry = ProviderRegistry()
