"""Single-use, installation-bound grants for local connector setup."""

import secrets
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from api_service.helpers import get_workspace_for_user
from api_service.mcp_adapter.auth import token_hash
from api_service.mcp_adapter.identity import installation_id
from backend_common.db import McpClient, McpPairing
from backend_common.settings import get_settings

PAIRING_TTL = 600


class PairingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    workspace_id: str = Field(min_length=1, max_length=128)
    access: str = "standard"
    require_approval: bool = False


class PairingRedeem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairing_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=32, max_length=128)
    installation_id: str = Field(min_length=1, max_length=128)


def check_scope(db: Session, user_id: str, workspace_id: str, access: str):
    get_workspace_for_user(db, workspace_id, user_id)
    if access not in {"read", "standard"} or (get_settings().mcp_access == "read" and access != "read"):
        raise HTTPException(400, "Access must be permitted by the launch profile")


def create_pairing(db: Session, user_id: str, body: PairingCreate, base_url: str) -> dict:
    check_scope(db, user_id, body.workspace_id, body.access)
    now = int(time.time())
    # Grants are useful only until redemption or expiry. Keeping them afterward
    # adds no diagnostic value and lets repeated setup attempts grow the table.
    db.query(McpPairing).filter(
        (McpPairing.consumed.is_(True)) | (McpPairing.expires_at <= now)
    ).delete(synchronize_session=False)
    code = "ncpair_" + secrets.token_urlsafe(32)
    row = McpPairing(user_id=user_id, workspace_id=body.workspace_id, name=body.name,
                     access=body.access, require_approval=body.require_approval,
                     code_hash=token_hash(code), installation_id=installation_id(),
                     expires_at=now + PAIRING_TTL)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"pairing_id": row.id, "code": code, "installation_id": row.installation_id,
            "url": base_url.rstrip("/") + "/mcp", "expires_at": row.expires_at}


def redeem_pairing(db: Session, body: PairingRedeem, base_url: str) -> dict:
    if body.installation_id != installation_id():
        raise HTTPException(409, "Pairing belongs to a different NeuroCade installation. Create a new setup prompt or extension.")
    # Claim and credential creation commit together; concurrent callers cannot both redeem.
    claimed = db.execute(update(McpPairing).where(
        McpPairing.id == body.pairing_id,
        McpPairing.code_hash == token_hash(body.code),
        McpPairing.installation_id == body.installation_id,
        McpPairing.expires_at > int(time.time()),
        McpPairing.consumed.is_(False),
    ).values(consumed=True).returning(McpPairing.id)).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        raise HTTPException(410, "Pairing expired, was already used, or is invalid. Create a new setup prompt or extension in NeuroCade.")
    row = db.get(McpPairing, claimed)
    assert row is not None
    try:
        check_scope(db, row.user_id, row.workspace_id, row.access)
        token = "ncmcp_" + secrets.token_urlsafe(32)
        client = McpClient(user_id=row.user_id, workspace_id=row.workspace_id, name=row.name,
                           access=row.access, require_approval=row.require_approval, token_hash=token_hash(token))
        db.add(client)
        db.commit()
        db.refresh(client)
    except Exception:
        db.rollback()
        raise
    return {"url": base_url.rstrip("/") + "/mcp", "installation_id": row.installation_id,
            "client_id": client.id, "token": token, "user_id": client.user_id, "workspace_id": client.workspace_id}
