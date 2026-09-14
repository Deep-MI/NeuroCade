"""Strict credentials and browser-origin boundary for local agent access."""

import hashlib
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.helpers import get_workspace_for_user
from api_service.policies import require_workspace_read
from backend_common.auth import AuthContext
from backend_common.db import McpClient, User, run_with_sqlite_lock_retry

current_client: ContextVar[str] = ContextVar("mcp_client")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_client(db: Session, client_id: str) -> tuple[McpClient, AuthContext]:
    client = db.get(McpClient, client_id)
    if client is None or client.revoked:
        raise HTTPException(401, "Client revoked or unavailable")
    user = db.get(User, client.user_id)
    if user is None:
        raise HTTPException(401, "Client user unavailable")
    _, role = get_workspace_for_user(db, client.workspace_id, user.id)
    require_workspace_read(role)
    return client, AuthContext(user=user, role=role, auth_mode="mcp")


def authenticate(db: Session, authorization: str) -> str:
    if not authorization.startswith("Bearer ") or len(authorization) > 1024:
        raise HTTPException(401, "MCP client credential required")
    def check_and_touch():
        # Authentication also writes last_seen. Read policy in the same short
        # write transaction so a worker commit cannot stale the SQLite snapshot.
        db.rollback()
        db.connection(execution_options={"sqlite_begin_immediate": True})
        client = db.query(McpClient).filter(McpClient.token_hash == token_hash(authorization[7:])).one_or_none()
        if client is None:
            raise HTTPException(401, "Invalid MCP client credential")
        resolve_client(db, client.id)
        client.last_seen = int(time.time())
        db.commit()
        return client.id

    return run_with_sqlite_lock_retry(db, check_and_touch)


def validate_local_headers(headers, *, management=False):
    host = headers.get("host", "")
    try:
        parsed = urlsplit("http://" + host)
        valid = parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.username is None and not parsed.path
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(403, "Local host required")
    origin = headers.get("origin")
    if origin and origin not in {"http://" + host, "https://" + host}:
        raise HTTPException(403, "Invalid origin")
    if management and headers.get("x-neurocade-ui") != "1":
        raise HTTPException(403, "NeuroCade UI request required")
