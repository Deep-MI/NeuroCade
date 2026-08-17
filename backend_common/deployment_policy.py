"""Provide shared backend deployment policy utilities for NeuroCade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend_common.settings import Settings, get_settings

DeploymentProfile = Literal["local", "internal", "demo"]
VALID_DEPLOYMENT_PROFILES: set[str] = {"local", "internal", "demo"}


@dataclass(frozen=True)
class DeploymentPolicy:
    profile: DeploymentProfile
    public_url: str
    uploads_enabled: bool
    destructive_actions_enabled: bool
    sample_data_scope: Literal["per_user", "global"]

    def feature_flags(self) -> dict[str, bool]:
        """Return the deployment controls consumed by the client."""
        return {
            "uploads": self.uploads_enabled,
            "destructive_actions": self.destructive_actions_enabled,
        }

    def validate_auth_configuration(self, settings: Settings) -> None:
        """Validate authentication settings required by this deployment."""
        if self.profile == "local":
            return

        if settings.local_auth_enabled:
            raise RuntimeError("LOCAL_AUTH_ENABLED must be false for internal and demo deployments.")
        if not settings.clerk_jwks_url:
            raise RuntimeError("CLERK_JWKS_URL must be configured for internal and demo deployments.")
        if not settings.clerk_issuer:
            raise RuntimeError("CLERK_ISSUER must be configured for internal and demo deployments.")
        if not settings.clerk_audience:
            raise RuntimeError("CLERK_AUDIENCE must be configured for internal and demo deployments.")


def deployment_profile(settings: Settings | None = None) -> DeploymentProfile:
    """Return the normalized, validated DEPLOYMENT_PROFILE value."""
    active_settings = settings or get_settings()
    raw_profile = (active_settings.deployment_profile or "").strip().lower()
    if raw_profile not in VALID_DEPLOYMENT_PROFILES:
        raise RuntimeError(f"Invalid DEPLOYMENT_PROFILE: {active_settings.deployment_profile!r}")
    return raw_profile  # type: ignore[return-value]


def get_deployment_policy(settings: Settings | None = None) -> DeploymentPolicy:
    """Build the deployment policy for the active settings."""
    active_settings = settings or get_settings()
    profile = deployment_profile(active_settings)
    public_url = active_settings.app_base_url
    if profile == "local":
        return DeploymentPolicy(
            profile="local",
            public_url=public_url,
            uploads_enabled=True,
            destructive_actions_enabled=True,
            sample_data_scope="per_user",
        )
    if profile == "internal":
        return DeploymentPolicy(
            profile="internal",
            public_url=public_url,
            uploads_enabled=True,
            destructive_actions_enabled=True,
            sample_data_scope="per_user",
        )
    return DeploymentPolicy(
        profile="demo",
        public_url=public_url,
        uploads_enabled=False,
        destructive_actions_enabled=False,
        sample_data_scope="global",
    )
