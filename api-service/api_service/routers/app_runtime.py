"""Serve app runtime resources and GUI state sync endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from api_service.deps import get_context, get_db
from api_service.gui_state import build_gui_state_session_key, resolve_gui_state_scope
from api_service.runtime.service import LUT_PATH, runtime_service
from api_service.schemas import GuiStateSyncRequest
from api_service.viewer_resources import resolve_gui_resource_descriptors
from backend_common.auth import AuthContext


router = APIRouter(prefix="/api/app", tags=["runtime"])


@router.get("/static/luts/freesurfer")
async def freesurfer_lut(_context: AuthContext = Depends(get_context)) -> Response:
    """Return the bundled FreeSurfer color lookup table."""
    if not LUT_PATH.is_file():
        raise HTTPException(status_code=404, detail="FreeSurferColorLUT.txt not found")
    return FileResponse(LUT_PATH, media_type="text/plain", filename="FreeSurferColorLUT.txt")


@router.post("/gui/state")
async def gui_state_sync(
    payload: GuiStateSyncRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Sync GUI state through the runtime service and resolve viewer resources."""
    workspace_id, case_id = resolve_gui_state_scope(
        db,
        context,
        workspace_id=payload.workspace_id,
        case_id=payload.case_id,
        current_case_id=payload.current_case_id,
    )
    gui_state_key = build_gui_state_session_key(
        user_id=context.user.id,
        workspace_id=workspace_id,
        case_id=case_id,
        gui_session_id=payload.gui_session_id,
    )
    sync_payload = payload.model_dump(
        exclude_none=True,
        exclude={"workspace_id", "case_id", "gui_session_id"},
    )
    if workspace_id:
        sync_payload["current_workspace_id"] = workspace_id
    response = await runtime_service.sync_gui_state(
        sync_payload,
        gui_state_key=gui_state_key,
    )
    return resolve_gui_resource_descriptors(db, context, response)
