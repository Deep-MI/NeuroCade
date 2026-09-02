"""Provide API service workspaces behavior for NeuroCade."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api_service.cases.operations import purge_case_rows
from api_service.deps import get_context, get_db
from api_service.helpers import get_workspace_for_user, log_event
from api_service.policies import require_workspace_manage
from api_service.runtime import settings
from api_service.schemas import (
    WorkspaceCreateRequest,
    WorkspaceDeleteRequest,
    WorkspaceSummary,
    WorkspaceUpdateRequest,
)
from backend_common.auth import AuthContext
from backend_common.case_storage import (
    ensure_workspace_storage_layout,
    rename_workspace_storage,
    resolve_workspace_storage,
    validate_workspace_name,
    workspace_storage_dir,
)
from backend_common.db import (
    Artifact,
    AssistantMessage,
    AssistantScope,
    AssistantThread,
    AssistantTurn,
    AuditEvent,
    Case,
    CaseEvent,
    RoleEnum,
    Run,
    Workspace,
    WorkspaceMembership,
)
from backend_common.deployment_policy import get_deployment_policy
from backend_common.run_statuses import ACTIVE_RUN_STATUSES
from backend_common.storage_transactions import finalize_staged_path, restore_staged_path, stage_path_for_deletion

router = APIRouter(prefix="/api/app", tags=["workspaces"])


def count_workspace_cases(db: Session, workspace_id: str) -> int:
    """Count cases in a workspace."""
    return int(
        db.query(func.count(Case.id))
        .filter(Case.workspace_id == workspace_id)
        .scalar()
        or 0
    )


def workspace_case_counts(db: Session, workspace_ids: list[str]) -> dict[str, int]:
    """Return case counts keyed by workspace ID."""
    if not workspace_ids:
        return {}
    rows = (
        db.query(Case.workspace_id, func.count(Case.id))
        .filter(Case.workspace_id.in_(workspace_ids))
        .group_by(Case.workspace_id)
        .all()
    )
    return {workspace_id: int(count) for workspace_id, count in rows}


def serialize_workspace(workspace: Workspace, role: RoleEnum, case_count: int = 0) -> WorkspaceSummary:
    """Build the API summary for a workspace and the current user's role."""
    return WorkspaceSummary(
        id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        role=role.value,
        kind=workspace.kind,
        is_default=workspace.is_default,
        case_count=case_count,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def validate_workspace_name_or_400(name: str) -> str:
    """Validate a workspace name or raise a 400 HTTP error."""
    try:
        return validate_workspace_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def ensure_workspace_cases_idle(db: Session, cases: list[Case]) -> None:
    """Reject workspace changes when any selected case has an active run."""
    active_case_ids: list[str] = []
    for case in cases:
        active_run = (
            db.query(Run)
            .filter(Run.case_id == case.id, Run.status.in_(ACTIVE_RUN_STATUSES))
            .order_by(Run.created_at.desc(), Run.id.desc())
            .first()
        )
        if active_run is not None:
            active_case_ids.append(case.id)
    if active_case_ids:
        raise HTTPException(status_code=409, detail=f"Cases already have active runs: {', '.join(active_case_ids)}")


def ensure_workspace_runs_idle(db: Session, workspace: Workspace) -> None:
    """Reject workspace identity changes while a workspace-scoped run is active."""
    active_run = (
        db.query(Run)
        .filter(
            Run.workspace_id == workspace.id,
            Run.scope_type == AssistantScope.workspace,
            Run.status.in_(ACTIVE_RUN_STATUSES),
        )
        .first()
    )
    if active_run is not None:
        raise HTTPException(status_code=409, detail="Workspace has an active run")


def require_workspace_mutations_enabled() -> None:
    """Require deployment policy to allow workspace mutations."""
    if not get_deployment_policy(settings).destructive_actions_enabled:
        raise HTTPException(status_code=403, detail="This action is disabled for this deployment")


@router.get("/workspaces", response_model=list[WorkspaceSummary])
def list_workspaces(
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[WorkspaceSummary]:
    """List workspaces visible to the current user."""
    memberships = (
        db.query(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(WorkspaceMembership.user_id == context.user.id)
        .order_by(Workspace.is_default.desc(), Workspace.created_at.asc())
        .all()
    )
    available_memberships = []
    for membership, workspace in memberships:
        try:
            resolve_workspace_storage(settings, workspace)
        except FileNotFoundError:
            continue
        available_memberships.append((membership, workspace))
    db.commit()
    memberships = available_memberships
    counts = workspace_case_counts(db, [workspace.id for _, workspace in memberships])
    return [serialize_workspace(workspace, membership.role, counts.get(workspace.id, 0)) for membership, workspace in memberships]


@router.post("/workspaces", response_model=WorkspaceSummary)
def create_workspace(
    request: WorkspaceCreateRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> WorkspaceSummary:
    """Create a shared workspace owned by the current user."""
    require_workspace_mutations_enabled()
    name = validate_workspace_name_or_400(request.name)
    workspace = Workspace(
        id=str(uuid4()),
        owner_user_id=context.user.id,
        name=name,
        description=(request.description or "").strip() or None,
        kind="shared",
        is_default=False,
    )
    db.add(workspace)
    db.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=context.user.id,
        role=RoleEnum.owner,
        granted_by_user_id=context.user.id,
    )
    db.add(membership)
    try:
        ensure_workspace_storage_layout(settings, workspace)
        db.commit()
    except FileExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(workspace)
    log_event(db, context, "workspace.created", details={"workspace_id": workspace.id, "name": workspace.name})
    return serialize_workspace(workspace, membership.role)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceSummary)
def update_workspace(
    workspace_id: str,
    request: WorkspaceUpdateRequest,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> WorkspaceSummary:
    """Update workspace metadata after permission checks."""
    require_workspace_mutations_enabled()
    workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_manage(role)

    if not request.model_fields_set:
        raise HTTPException(status_code=400, detail="No workspace updates requested")

    if request.name is not None:
        name = validate_workspace_name_or_400(request.name)
        if name != workspace.name:
            cases = (
                db.query(Case)
                .filter(Case.workspace_id == workspace.id)
                .order_by(Case.id.asc())
                .all()
            )
            ensure_workspace_cases_idle(db, cases)
            ensure_workspace_runs_idle(db, workspace)
            try:
                rename_workspace_storage(settings, workspace.id, name)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        workspace.name = name

    if "description" in request.model_fields_set:
        workspace.description = (request.description or "").strip() or None

    db.commit()
    db.refresh(workspace)
    log_event(db, context, "workspace.updated", details={"workspace_id": workspace.id, "name": workspace.name})
    return serialize_workspace(workspace, role, count_workspace_cases(db, workspace.id))


@router.delete("/workspaces/{workspace_id}", response_model=dict)
def delete_workspace(
    workspace_id: str,
    request: WorkspaceDeleteRequest | None = None,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> dict:
    """Delete a workspace and all stored data it owns."""
    require_workspace_mutations_enabled()
    workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_manage(role)
    if workspace.is_default:
        raise HTTPException(status_code=400, detail="Default workspaces cannot be deleted")

    cases = (
        db.query(Case)
        .filter(Case.workspace_id == workspace.id)
        .order_by(Case.id.asc())
        .all()
    )
    if cases and not (request and request.confirm_non_empty_delete):
        raise HTTPException(status_code=409, detail="Workspace still contains cases; confirm deletion to delete it")
    ensure_workspace_cases_idle(db, cases)
    ensure_workspace_runs_idle(db, workspace)

    staged_storage = stage_path_for_deletion(
        workspace_storage_dir(settings, workspace.id),
        settings.outputs_dir / ".trash" / "workspaces",
    )
    try:
        for case in cases:
            purge_case_rows(db, case)

        workspace_artifact_ids = [
            artifact_id
            for (artifact_id,) in db.query(Artifact.id)
            .filter(Artifact.workspace_id == workspace.id, Artifact.case_id.is_(None))
            .all()
        ]
        if workspace_artifact_ids:
            db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(workspace_artifact_ids)).delete(synchronize_session=False)
            db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(workspace_artifact_ids)).delete(synchronize_session=False)
        db.query(CaseEvent).filter(CaseEvent.workspace_id == workspace.id).delete(synchronize_session=False)
        db.query(Artifact).filter(Artifact.workspace_id == workspace.id).delete(synchronize_session=False)
        db.query(Run).filter(Run.workspace_id == workspace.id).delete(synchronize_session=False)
        db.query(AssistantMessage).filter(AssistantMessage.workspace_id == workspace.id).delete(synchronize_session=False)
        db.query(AssistantTurn).filter(AssistantTurn.workspace_id == workspace.id).delete(synchronize_session=False)
        db.query(AssistantThread).filter(AssistantThread.workspace_id == workspace.id).delete(synchronize_session=False)
        db.query(WorkspaceMembership).filter(WorkspaceMembership.workspace_id == workspace.id).delete(synchronize_session=False)
        db.delete(workspace)
        db.commit()
    except Exception:
        db.rollback()
        restore_staged_path(staged_storage)
        raise
    finalize_staged_path(staged_storage)
    log_event(db, context, "workspace.deleted", details={"deleted_workspace_id": workspace_id})
    return {"deleted": workspace_id}
