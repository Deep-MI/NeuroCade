"""Application-owned pairing and approval. MCP credentials cannot administer themselves."""

import base64
import json
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from starlette.responses import Response

from api_service.deps import get_context, get_db
from api_service.mcp_adapter.auth import validate_local_headers
from api_service.mcp_adapter.pairing import PairingCreate, PairingRedeem, create_pairing, redeem_pairing
from api_service.mcp_adapter.service import decide, serialize
from backend_common.auth import AuthContext
from backend_common.db import AssistantToolExecution, McpClient
from backend_common.settings import get_settings


def ui_guard(request: Request):
    settings = get_settings()
    if not settings.mcp_enabled:
        raise HTTPException(404, "Local agent access disabled; launch with --mcp")
    validate_local_headers(request.headers, management=True)
    # MCP tokens are not UI credentials, even with local auth fallback enabled.
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ncmcp_"):
        raise HTTPException(403, "Agent credentials cannot manage clients or approvals")


router = APIRouter(prefix="/api/app/mcp", dependencies=[Depends(ui_guard)])


@router.post("/pairings")
def pair(body: PairingCreate, request: Request, response: Response, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    response.headers["Cache-Control"] = "no-store"
    return create_pairing(db, context.user.id, body, str(request.base_url))


@router.post("/pairings/redeem")
def redeem(body: PairingRedeem, request: Request, response: Response, db: Session = Depends(get_db)):
    # The one-time grant supplies authorization. Never fall back to a UI login here.
    response.headers["Cache-Control"] = "no-store"
    return redeem_pairing(db, body, str(request.base_url))


@router.post("/desktop-extension")
def desktop_extension(body: PairingCreate, request: Request, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    from api_service.mcp_adapter.extension import desktop_bundle, host_executable

    host_executable()  # Reject an unavailable host before issuing the grant.
    pairing = create_pairing(db, context.user.id, body, str(request.base_url))
    return Response(desktop_bundle(pairing), media_type="application/octet-stream", headers={
        "Content-Disposition": 'attachment; filename="neurocade.mcpb"',
        "Cache-Control": "no-store",
    })


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


class ConnectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_approval: bool


def summary(client):
    return {
        "id": client.id,
        "name": client.name,
        "workspace_id": client.workspace_id,
        "access": client.access,
        "require_approval": client.require_approval,
        "revoked": client.revoked,
        "last_seen": client.last_seen,
    }


@router.get("/status")
def status():
    return {
        "enabled": True,
        "host_executable": os.environ.get("NEUROCADE_MCP_HOST_EXECUTABLE", ""),
        "access": get_settings().mcp_access,
        "launch_id": os.environ.get("NEUROCADE_LAUNCH_ID", "development"),
    }


@router.get("/clients")
def clients(
    after: str | None = Query(None, max_length=512),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
):
    return page(db.query(McpClient).filter_by(user_id=context.user.id), McpClient, after, limit, summary)


@router.delete("/clients/{client_id}")
def revoke(client_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    client = db.get(McpClient, client_id)
    if client is None or client.user_id != context.user.id:
        raise HTTPException(404, "Client not found")
    client.revoked = True
    db.query(AssistantToolExecution).filter_by(client_id=client.id, status="planned").update({"status": "denied"})
    db.commit()
    return {"revoked": True}


@router.patch("/clients/{client_id}")
def update_policy(client_id: str, body: ConnectionPolicy, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    client = db.get(McpClient, client_id)
    if client is None or client.user_id != context.user.id or client.revoked:
        raise HTTPException(404, "Connection unavailable; pair again")
    client.require_approval = body.require_approval
    db.commit()
    return summary(client)


def page(query, model, after, limit, project):
    if after:
        try:
            timestamp, identity = json.loads(base64.urlsafe_b64decode(after).decode())
            created = datetime.fromisoformat(timestamp)
            if not isinstance(identity, str):
                raise ValueError("Invalid cursor")
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(400, "Invalid page cursor") from None
        query = query.filter(
            or_(
                func.julianday(model.created_at) < func.julianday(created),
                and_(func.julianday(model.created_at) == func.julianday(created), model.id < identity),
            )
        )
    rows = query.order_by(func.julianday(model.created_at).desc(), model.id.desc()).limit(limit + 1).all()
    cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        cursor = base64.urlsafe_b64encode(json.dumps([last.created_at.isoformat(), last.id]).encode()).decode()
    return {"items": [project(row) for row in rows[:limit]], "next_cursor": cursor}


def activity_summary(row):
    return {key: value for key, value in serialize(row).items() if key != "result"}


@router.get("/invocations")
def invocations(
    after: str | None = Query(None, max_length=512),
    limit: int = Query(50, ge=1, le=100),
    pending: bool = False,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
):
    query = db.query(AssistantToolExecution).filter_by(user_id=context.user.id, source="mcp")
    awaiting = and_(AssistantToolExecution.status == "planned", AssistantToolExecution.expires_at > int(time.time()))
    query = query.filter(awaiting if pending else ~awaiting)
    return page(query, AssistantToolExecution, after, limit, activity_summary)


@router.get("/invocations/{invocation_id}")
def invocation(invocation_id: str, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    row = db.get(AssistantToolExecution, invocation_id)
    if row is None or row.source != "mcp" or row.user_id != context.user.id:
        raise HTTPException(404, "Invocation not found")
    return activity_summary(row)


@router.post("/invocations/{invocation_id}/decision")
async def decision(invocation_id: str, body: Decision, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)):
    row = db.get(AssistantToolExecution, invocation_id)
    if row is None or row.source != "mcp" or row.user_id != context.user.id:
        raise HTTPException(404, "Invocation not found")
    return await decide(db, row, body.approve)
