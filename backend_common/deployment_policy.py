"""Provide shared backend deployment policy utilities for NeuroCade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend_common.settings import Settings, get_settings


DeploymentProfile = Literal["local", "internal", "demo"]
VALID_DEPLOYMENT_PROFILES: set[str] = {"local", "internal", "demo"}
_INSECURE_CREDENTIAL_VALUES = frozenset(
    {
        "",
        "CHANGE_ME",
        "CHANGE_ME_LONG_RANDOM",
        "NOTSET",
        "fastsurfer",
    }
)


def _configured_secret(value: str | None) -> str:
    return (value or "").strip()


def _url_contains_insecure_credential(url: str | None) -> bool:
    value = _configured_secret(url)
    if not value:
        return False
    return any(f":{credential}@" in value for credential in _INSECURE_CREDENTIAL_VALUES if credential)


@dataclass(frozen=True)
class DeploymentPolicy:
    profile: DeploymentProfile
    public_url: str
    auth_required: bool
    uploads_enabled: bool
    destructive_actions_enabled: bool
    monitoring_visible: bool
    production_checks_required: bool
    sample_data_enabled: bool
    sample_data_scope: Literal["per_user", "global"]
    runtime_network_disabled: bool = True

    @property
    def demo_mode(self) -> bool:
        """Whether this policy uses the public demo profile."""
        return self.profile == "demo"

    def feature_flags(self, *, clerk_configured: bool, monitoring_admin: bool = False) -> dict[str, bool]:
        """Return client-visible feature flags for this deployment."""
        return {
            "clerk": clerk_configured,
            "workflows": True,
            "uploads": self.uploads_enabled,
            "destructive_actions": self.destructive_actions_enabled,
            "sample_data": self.sample_data_enabled,
            "demo_mode": self.demo_mode,
            "monitoring_dashboard": self.monitoring_visible and monitoring_admin,
        }

    def limits(self, settings: Settings) -> dict[str, int]:
        """Return upload and DICOM ingestion limits from settings."""
        return {
            "max_upload_file_size_bytes": settings.max_upload_file_size_bytes,
            "dicom_zip_max_entries": settings.dicom_zip_max_entries,
            "dicom_zip_max_expanded_bytes": settings.dicom_zip_max_expanded_bytes,
        }

    def sample_data(self) -> dict[str, Any]:
        """Return sample dataset availability and provenance metadata."""
        return {
            "enabled": self.sample_data_enabled,
            "scope": self.sample_data_scope,
            "label": "Sample/de-identified data",
            "provenance": "Rhineland T1-weighted sample processed with FastSurfer and seeded by NeuroCade.",
            "modifiable_copy": self.sample_data_scope == "per_user",
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
        self.validate_production_credentials(settings)

    def validate_production_credentials(self, settings: Settings) -> None:
        """Reject development database credentials in shared deployment profiles."""
        if not self.production_checks_required:
            return

        # SQLite is a local file with no credentials; only an externally
        # configured DATABASE_URL could carry insecure embedded credentials.
        if _url_contains_insecure_credential(settings.database_url):
            raise RuntimeError("DATABASE_URL must not contain default database credentials for internal and demo deployments.")


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
    public_url = active_settings.app_public_url or active_settings.app_base_url
    if profile == "local":
        return DeploymentPolicy(
            profile="local",
            public_url=public_url,
            auth_required=False,
            uploads_enabled=True,
            destructive_actions_enabled=True,
            monitoring_visible=True,
            production_checks_required=False,
            sample_data_enabled=True,
            sample_data_scope="per_user",
        )
    if profile == "internal":
        return DeploymentPolicy(
            profile="internal",
            public_url=public_url,
            auth_required=True,
            uploads_enabled=True,
            destructive_actions_enabled=True,
            monitoring_visible=True,
            production_checks_required=True,
            sample_data_enabled=True,
            sample_data_scope="per_user",
        )
    return DeploymentPolicy(
        profile="demo",
        public_url=public_url,
        auth_required=True,
        uploads_enabled=False,
        destructive_actions_enabled=False,
        monitoring_visible=False,
        production_checks_required=True,
        sample_data_enabled=True,
        sample_data_scope="global",
    )
