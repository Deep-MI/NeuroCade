"""Application middleware registration for the API service."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from api_service.monitoring.events import record_app_event
from api_service.runtime import logger, settings
from backend_common.db import SessionLocal
from backend_common.deployment_policy import get_deployment_policy

_LOCAL_ONLY_PATHS = frozenset(
    {
        "/api/app/openapi.json",
        "/api/app/docs",
    }
)


def _is_local_profile() -> bool:
    """Return whether the app is running with the local deployment policy."""
    return get_deployment_policy(settings).profile == "local"


def _allowed_hosts() -> set[str]:
    """Return hostnames accepted by hardened deployment profiles."""
    configured = {host.strip().lower() for host in settings.app_allowed_hosts.split(",") if host.strip()}
    if configured:
        return configured
    policy = get_deployment_policy(settings)
    hosts = {"127.0.0.1", "localhost"}
    try:
        from urllib.parse import urlparse

        parsed = urlparse(policy.public_url)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    except Exception:
        pass
    return hosts


def register_app_middleware(app: FastAPI) -> None:
    """Attach API service hardening and monitoring middleware."""

    @app.middleware("http")
    async def block_local_only_surfaces_outside_local_profile(request: Request, call_next):
        """Hide OpenAPI and documentation routes outside the local profile."""
        if request.url.path in _LOCAL_ONLY_PATHS and not _is_local_profile():
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return await call_next(request)

    @app.middleware("http")
    async def apply_http_hardening(request: Request, call_next):
        """Validate host headers and attach security headers for hardened profiles."""
        policy = get_deployment_policy(settings)
        if policy.profile in {"internal", "demo"}:
            host = (request.headers.get("host") or "").split(":", 1)[0].lower()
            if host and host not in _allowed_hosts():
                return JSONResponse(status_code=400, content={"detail": "Invalid host header"})
        response = await call_next(request)
        if policy.profile in {"internal", "demo"}:
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.clerk.accounts.dev https://*.clerk.com; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' https://*.clerk.accounts.dev https://*.clerk.com; "
                "worker-src 'self' blob:; frame-src https://*.clerk.accounts.dev https://*.clerk.com; object-src 'none'; base-uri 'self'",
            )
            if policy.public_url.startswith("https://"):
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.middleware("http")
    async def record_server_errors(request: Request, call_next):
        """Capture backend exceptions and 5xx responses as monitoring events."""
        skip_event_capture = request.url.path.startswith("/api/app/monitoring/client-errors")
        try:
            response = await call_next(request)
        except Exception as exc:
            if not skip_event_capture:
                try:
                    with SessionLocal() as db:
                        record_app_event(
                            db,
                            source="backend",
                            level="error",
                            event_type="backend.exception",
                            message=str(exc) or exc.__class__.__name__,
                            method=request.method,
                            path=request.url.path,
                            status_code=500,
                            details={"exception_type": exc.__class__.__name__},
                        )
                except Exception as log_exc:  # pragma: no cover - logging must not mask the real error
                    logger.warning("Failed to record backend exception event: %s", log_exc)
            raise

        if response.status_code >= 500 and not skip_event_capture:
            try:
                with SessionLocal() as db:
                    record_app_event(
                        db,
                        source="backend",
                        level="error",
                        event_type="backend.http_error",
                        message=f"{request.method} {request.url.path} returned HTTP {response.status_code}",
                        method=request.method,
                        path=request.url.path,
                        status_code=response.status_code,
                    )
            except Exception as exc:  # pragma: no cover - logging must not break responses
                logger.warning("Failed to record backend HTTP error event: %s", exc)
        return response
