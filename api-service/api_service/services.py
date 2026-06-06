"""Helpers for loading workspace runs with user authorization checks."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.helpers import get_workspace_for_user, resolve_case_role_for_user
from api_service.policies import require_case_read, require_workspace_read
from backend_common.db import AssistantScope, RoleEnum, Run
from backend_common.runs import is_workspace_run


def get_run_for_user(db: Session, run_id: str, user_id: str) -> tuple[Run, RoleEnum]:
    """Return a workspace run and the user's role after scope-based access checks.

    Parameters
    ----------
    db : Session
        Database session used to query run metadata.
    run_id : str
        Public run identifier to resolve.
    user_id : str
        User whose workspace or case permissions are evaluated.

    Returns
    -------
    tuple[Run, RoleEnum]
        Authorized workspace run and the user's effective role.
    """
    parent_run = db.query(Run).filter(Run.id == run_id).one_or_none()
    if parent_run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if parent_run.scope_type == AssistantScope.workspace or parent_run.case_id is None:
        _workspace, role = get_workspace_for_user(db, parent_run.workspace_id, user_id)
        role = require_workspace_read(role, detail="Run not found")
        return parent_run, role
    role, _workspace = resolve_case_role_for_user(db, parent_run.case_id, user_id)
    role = require_case_read(role, detail="Run not found")
    return parent_run, role


def get_workspace_batch_run_for_user(
    db: Session,
    workspace_id: str,
    run_id: str,
    user_id: str,
) -> tuple[Run, RoleEnum]:
    """Return an authorized workspace batch run for the requested workspace.

    Parameters
    ----------
    db : Session
        Database session used to query run metadata.
    workspace_id : str
        Workspace that must contain the run.
    run_id : str
        Public run identifier to resolve.
    user_id : str
        User whose workspace permissions are evaluated.

    Returns
    -------
    tuple[Run, RoleEnum]
        Authorized workspace batch run and the user's workspace role.
    """
    _workspace, role = get_workspace_for_user(db, workspace_id, user_id)
    require_workspace_read(role, detail="Workspace run not found")
    parent_run = (
        db.query(Run)
        .filter(
            Run.workspace_id == workspace_id,
            Run.id == run_id,
        )
        .one_or_none()
    )
    if parent_run is None or not is_workspace_run(parent_run):
        raise HTTPException(status_code=404, detail="Workspace run not found")
    return parent_run, role
