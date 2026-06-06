"""Provide API service gui state behavior for NeuroCade."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.helpers import get_case_for_user, get_workspace_for_user
from backend_common.auth import AuthContext
from backend_common.db import Case, Workspace, WorkspaceMembership


def build_gui_state_session_key(
    *,
    user_id: str | None,
    workspace_id: str | None,
    case_id: str | None,
    gui_session_id: str | None,
) -> str:
    """Build a stable cache key for a user's GUI state session.

    Parameters
    ----------
    user_id : str | None
        User that owns the GUI state, or anonymous when unavailable.
    workspace_id : str | None
        Workspace associated with the GUI state.
    case_id : str | None
        Case associated with the GUI state.
    gui_session_id : str | None
        Browser or client session identifier.

    Returns
    -------
    str
        Normalized key containing user, workspace, case, and session segments.
    """
    normalized_user_id = str(user_id or "anonymous").strip() or "anonymous"
    normalized_workspace_id = str(workspace_id or "-").strip() or "-"
    normalized_case_id = str(case_id or "-").strip() or "-"
    normalized_session_id = str(gui_session_id or "default").strip() or "default"
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
    current_case_id: str | None = None,
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
    current_case_id : str | None
        Active case used when an explicit case id is not supplied.

    Returns
    -------
    tuple[str, str | None]
        Authorized workspace id and optional case id.
    """
    normalized_workspace_id = str(workspace_id or "").strip() or None
    normalized_case_id = str(case_id or "").strip() or None
    normalized_current_case_id = str(current_case_id or "").strip() or None

    if normalized_case_id is not None:
        case, _role = get_case_for_user(
            db,
            normalized_case_id,
            context.user.id,
            workspace_id=normalized_workspace_id,
        )
        normalized_workspace_id = case.workspace_id
    elif normalized_current_case_id is not None:
        case = (
            db.query(Case)
            .join(Workspace, Workspace.id == Case.workspace_id)
            .join(
                WorkspaceMembership,
                (WorkspaceMembership.workspace_id == Case.workspace_id)
                & (WorkspaceMembership.user_id == context.user.id),
            )
            .filter(
                Case.id == normalized_current_case_id,
                Workspace.status == "active",
            )
            .one_or_none()
        )
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        normalized_case_id = case.id
        normalized_workspace_id = case.workspace_id
    elif normalized_workspace_id is not None:
        get_workspace_for_user(db, normalized_workspace_id, context.user.id)
    else:
        raise HTTPException(status_code=400, detail="workspace_id or case_id is required for GUI state sync")

    return normalized_workspace_id, normalized_case_id
