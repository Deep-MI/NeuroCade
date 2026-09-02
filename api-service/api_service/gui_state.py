"""Provide API service gui state behavior for NeuroCade."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.helpers import get_case_for_user, get_workspace_for_user
from backend_common.auth import AuthContext


def build_gui_state_session_key(
    *,
    user_id: str,
    workspace_id: str,
    case_id: str | None,
    gui_session_id: str,
) -> str:
    """Build a stable cache key for a user's GUI state session.

    Parameters
    ----------
    user_id : str
        User that owns the GUI state.
    workspace_id : str
        Workspace associated with the GUI state.
    case_id : str | None
        Case associated with the GUI state.
    gui_session_id : str
        Browser or client session identifier.

    Returns
    -------
    str
        Normalized key containing user, workspace, case, and session segments.
    """
    normalized_user_id = str(user_id).strip()
    normalized_workspace_id = str(workspace_id).strip()
    normalized_case_id = str(case_id or "-").strip() or "-"
    normalized_session_id = str(gui_session_id).strip()
    if not normalized_user_id or not normalized_workspace_id or not normalized_session_id:
        raise ValueError("user_id, workspace_id, and gui_session_id are required")
    return (
        f"user:{normalized_user_id}|workspace:{normalized_workspace_id}"
        f"|case:{normalized_case_id}|session:{normalized_session_id}"
    )


def resolve_gui_state_scope(
    db: Session,
    context: AuthContext,
    *,
    workspace_id: str | None,
    case_id: str | None,
) -> tuple[str, str | None]:
    """Resolve and authorize the workspace and case for GUI state sync.

    Parameters
    ----------
    db : Session
        Database session used to load workspace and case records.
    context : AuthContext
        Authenticated user context for access checks.
    workspace_id : str | None
        Requested workspace scope.
    case_id : str | None
        Requested case scope.
    Returns
    -------
    tuple[str, str | None]
        Authorized workspace id and optional case id.
    """
    normalized_workspace_id = str(workspace_id or "").strip() or None
    normalized_case_id = str(case_id or "").strip() or None
    if normalized_case_id is not None:
        case, _workspace, _role, _case_dir = get_case_for_user(
            db,
            normalized_case_id,
            context.user.id,
            workspace_id=normalized_workspace_id,
        )
        normalized_workspace_id = case.workspace_id
    elif normalized_workspace_id is not None:
        get_workspace_for_user(db, normalized_workspace_id, context.user.id)
    else:
        raise HTTPException(status_code=400, detail="workspace_id is required for GUI state sync")

    return normalized_workspace_id, normalized_case_id
