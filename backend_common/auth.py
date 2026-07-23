"""Provide shared backend auth utilities for NeuroCade."""

import json
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from sqlalchemy.orm import Session

from backend_common.db import RoleEnum, User
from backend_common.deployment_policy import get_deployment_policy
from backend_common.sample_seed import ensure_global_sample_workspace_membership, ensure_sample_case
from backend_common.settings import get_settings
from backend_common.workspace_bootstrap import ensure_personal_workspace

settings = get_settings()
logger = logging.getLogger(__name__)
_BOOTSTRAP_LOCKS: dict[str, threading.RLock] = {}
_BOOTSTRAP_LOCKS_GUARD = threading.Lock()


@dataclass
class AuthContext:
    user: User
    role: RoleEnum
    auth_mode: str


@contextmanager
def _user_bootstrap_lock(_db: Session, user_id: str):
    """Serialize a user's bootstrap work within this single-process monolith."""
    with _BOOTSTRAP_LOCKS_GUARD:
        lock = _BOOTSTRAP_LOCKS.setdefault(user_id, threading.RLock())
    with lock:
        yield


def _commit_auth_bootstrap(db: Session) -> None:
    """Commit auth bootstrap changes, rolling back on failure."""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _fetch_clerk_user_profile(user_id: str) -> dict[str, str | None]:
    """Fetch email and display-name fields from Clerk for a user."""
    if not settings.clerk_secret_key:
        return {"email": None, "full_name": None}

    request = Request(
        f"https://api.clerk.com/v1/users/{user_id}",
        headers={
            "Authorization": f"Bearer {settings.clerk_secret_key}",
            "Accept": "application/json",
            "User-Agent": "@clerk/backend@3.2.10",
            "Clerk-API-Version": "2025-11-10",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("Failed to fetch Clerk profile for %s: %s", user_id, exc)
        return {"email": None, "full_name": None}

    primary_email_id = payload.get("primary_email_address_id")
    email_addresses = payload.get("email_addresses") or []
    email = None
    for item in email_addresses:
        if item.get("id") == primary_email_id and item.get("email_address"):
            email = item["email_address"]
            break
    if not email:
        for item in email_addresses:
            if item.get("email_address"):
                email = item["email_address"]
                break

    name_parts = [payload.get("first_name"), payload.get("last_name")]
    full_name = " ".join(part.strip() for part in name_parts if isinstance(part, str) and part.strip()).strip()
    if not full_name:
        full_name = payload.get("username") or email or user_id

    return {"email": email, "full_name": full_name}


def _upsert_local_user(db: Session) -> AuthContext:
    """Ensure the configured local user exists."""
    policy = get_deployment_policy(settings)
    with _user_bootstrap_lock(db, settings.local_auth_user_id):
        user = db.get(User, settings.local_auth_user_id)
        if user is None:
            user = User(id=settings.local_auth_user_id)
            db.add(user)
        user.external_auth_id = settings.local_auth_user_id
        user.email = settings.local_auth_email
        user.full_name = settings.local_auth_name
        db.flush()
        ensure_personal_workspace(db, user)
        if policy.sample_data_scope == "per_user":
            ensure_sample_case(db, user)
        elif policy.sample_data_scope == "global":
            ensure_global_sample_workspace_membership(db, user)
        _commit_auth_bootstrap(db)
        return AuthContext(user=user, role=RoleEnum.owner, auth_mode="local")


def _verify_clerk_token(token: str) -> dict:
    """Validate a Clerk JWT and return its decoded claims."""
    if not settings.clerk_jwks_url:
        raise HTTPException(status_code=500, detail="CLERK_JWKS_URL is not configured")
    audience = (settings.clerk_audience or "").strip() or None
    try:
        jwks_client = PyJWKClient(settings.clerk_jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            audience=audience,
            options={"verify_aud": bool(audience)},
        )
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication token") from exc


def allow_local_auth() -> bool:
    """Return whether local auth fallback is enabled."""
    return settings.local_auth_enabled and get_deployment_policy(settings).profile == "local"


def validate_auth_configuration() -> None:
    """Validate auth settings for the active deployment policy."""
    policy = get_deployment_policy(settings)
    policy.validate_auth_configuration(settings)
    if policy.profile == "local" and settings.clerk_jwks_url and not settings.clerk_audience:
        logger.warning(
            "CLERK_AUDIENCE is unset in local mode. Leave it blank only while Clerk session tokens "
            "omit an aud claim; set it to the exact JWT audience before relying on production-style auth."
        )


def get_auth_context(
    db: Session,
    authorization: str | None = None,
) -> AuthContext:
    """Resolve the authenticated user from Clerk or local auth."""
    if authorization and authorization.startswith("Bearer ") and settings.clerk_jwks_url:
        token = authorization.split(" ", 1)[1]
        claims = _verify_clerk_token(token)
        user_id = str(claims.get("sub"))
        profile = _fetch_clerk_user_profile(user_id)
        email = claims.get("email") or claims.get("email_address") or profile["email"]
        full_name = claims.get("name") or profile["full_name"] or email or user_id
        policy = get_deployment_policy(settings)
        with _user_bootstrap_lock(db, user_id):
            user = db.get(User, user_id)
            if user is None:
                user = User(
                    id=user_id,
                    external_auth_id=user_id,
                    email=email or f"{user_id}@unknown.local",
                    full_name=full_name,
                )
                db.add(user)
                db.flush()
                ensure_personal_workspace(db, user, readable_user_slug=True)
                if policy.sample_data_scope == "per_user":
                    ensure_sample_case(db, user)
                elif policy.sample_data_scope == "global":
                    ensure_global_sample_workspace_membership(db, user)
                _commit_auth_bootstrap(db)
                return AuthContext(user=user, role=RoleEnum.owner, auth_mode="clerk")

            if email and user.email != email:
                user.email = email
            if full_name and user.full_name != full_name:
                user.full_name = full_name
            ensure_personal_workspace(db, user, readable_user_slug=True)
            if policy.sample_data_scope == "per_user":
                ensure_sample_case(db, user)
            elif policy.sample_data_scope == "global":
                ensure_global_sample_workspace_membership(db, user)
            _commit_auth_bootstrap(db)
            return AuthContext(user=user, role=RoleEnum.owner, auth_mode="clerk")

    if allow_local_auth():
        return _upsert_local_user(db)

    raise HTTPException(status_code=401, detail="Authentication required")
