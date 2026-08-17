"""Provide API service providers behavior for NeuroCade."""

from fastapi import APIRouter, Depends

from api_service.deps import get_context
from api_service.schemas import ProviderSummary
from backend_common.auth import AuthContext
from backend_common.providers import provider_reachability, provider_registry

router = APIRouter(prefix="/api/app/providers", tags=["providers"])


@router.get("", response_model=list[ProviderSummary])
def list_providers(context: AuthContext = Depends(get_context)) -> list[ProviderSummary]:
    """Return configured provider models and their availability metadata."""
    summaries = []
    for cfg in provider_registry.list_models():
        reachable, reachability_reason = provider_reachability(cfg)
        summaries.append(ProviderSummary(
            provider=cfg.provider,
            provider_family=cfg.provider_family,
            model=cfg.model,
            is_default=provider_registry.is_default_provider(cfg.provider),
            vision=cfg.vision,
            configured=cfg.configured,
            reachable=reachable,
            configuration_reason=cfg.configuration_reason,
            reachability_reason=reachability_reason,
        ))
    return summaries
