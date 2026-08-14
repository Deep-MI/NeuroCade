"""Provide API service providers behavior for NeuroCade."""

from fastapi import APIRouter, Depends

from api_service.deps import get_context
from api_service.schemas import ProviderSummary
from backend_common.auth import AuthContext
from backend_common.providers import provider_registry

router = APIRouter(prefix="/api/app/providers", tags=["providers"])


@router.get("", response_model=list[ProviderSummary])
def list_providers(context: AuthContext = Depends(get_context)) -> list[ProviderSummary]:
    """Return configured provider models and their availability metadata."""
    return [
        ProviderSummary(
            provider=cfg.provider,
            provider_family=cfg.provider_family,
            model=cfg.model,
            is_default=provider_registry.is_default_provider(cfg.provider),
            vision=cfg.vision,
            available=cfg.available,
            availability_reason=cfg.availability_reason,
        )
        for cfg in provider_registry.list_models()
    ]
