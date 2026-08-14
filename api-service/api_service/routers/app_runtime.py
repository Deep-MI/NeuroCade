"""Serve app runtime resources and GUI state sync endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from api_service.deps import get_context, get_db
from api_service.gui_state import build_gui_state_session_key, resolve_gui_state_scope
from api_service.runtime import settings
from api_service.runtime.gui_runtime import LUT_PATH, gui_runtime
from api_service.runtime_tools.workflow_catalog import run_analysis_workflows_payload
from api_service.schemas import AnalysisToolSummary, GuiStateSyncRequest
from api_service.viewer_resources import resolve_gui_resource_descriptors
from backend_common.auth import AuthContext

router = APIRouter(prefix="/api/app", tags=["runtime"])


@router.get("/static/luts/freesurfer")
async def freesurfer_lut(_context: AuthContext = Depends(get_context)) -> Response:
    """Return the bundled FreeSurfer color lookup table."""
    if not LUT_PATH.is_file():
        raise HTTPException(status_code=404, detail="FreeSurferColorLUT.txt not found")
    return FileResponse(LUT_PATH, media_type="text/plain", filename="FreeSurferColorLUT.txt")


@router.get("/analysis-tools", response_model=list[AnalysisToolSummary])
async def analysis_tools(context: AuthContext = Depends(get_context)) -> list[AnalysisToolSummary]:
    """Return catalog workflows visible in the Run Analysis UI."""
    return [
        AnalysisToolSummary(**tool)
        for tool in run_analysis_workflows_payload(settings=settings, user_id=context.user.id)
    ]


@router.post("/gui/state")
async def gui_state_sync(
    payload: GuiStateSyncRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Sync GUI state through the GUI runtime and resolve viewer resources."""
    workspace_id, case_id = resolve_gui_state_scope(
        db,
        context,
        workspace_id=payload.workspace_id,
        case_id=payload.case_id,
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
    sync_payload["workspace_id"] = workspace_id
    sync_payload["case_id"] = case_id
    response = gui_runtime.sync_gui_state(
        sync_payload,
        gui_state_key=gui_state_key,
    )
    return resolve_gui_resource_descriptors(db, context, response)
