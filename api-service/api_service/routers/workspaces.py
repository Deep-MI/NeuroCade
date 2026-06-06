"""Provide API service workspaces behavior for NeuroCade."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api_service.cases.identity import rewrite_workspace_identity, rollback_path_move
from api_service.cases.operations import purge_case_rows_and_storage
from api_service.deps import get_context, get_db
from api_service.helpers import get_workspace_for_user, log_event
from api_service.policies import require_workspace_manage, require_workspace_write
from api_service.runtime import settings
from api_service.schemas import (
    WorkspaceBatchRunDetail,
    WorkspaceBatchRunSummary,
    WorkspaceCreateRequest,
    WorkspaceDeleteRequest,
    WorkspaceSummary,
    WorkspaceUpdateRequest,
)
from api_service.services import get_workspace_batch_run_for_user
from api_service.workspace_batch import cancel_workspace_batch_run, list_workspace_batch_runs, serialize_workspace_batch_detail
from backend_common.auth import AuthContext
from backend_common.case_storage import delete_workspace_storage, validate_workspace_name
from backend_common.concurrency import lock_cases_for_update, lock_workspace_for_update
from backend_common.deployment_policy import get_deployment_policy
from backend_common.db import (
    Artifact,
    AssistantCheckpoint,
    AssistantMessage,
    AssistantThread,
    AuditEvent,
    Case,
    CaseEvent,
    RoleEnum,
    Run,
    AssistantScope,
    Workspace,
    WorkspaceMembership,
)
from backend_common.run_statuses import ACTIVE_RUN_STATUSES


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
        status=workspace.status,
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
    """List active workspaces visible to the current user."""
    memberships = (
        db.query(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .filter(WorkspaceMembership.user_id == context.user.id, Workspace.status == "active")
        .order_by(Workspace.is_default.desc(), Workspace.created_at.asc())
        .all()
    )
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
    if db.get(Workspace, name) is not None:
        raise HTTPException(status_code=409, detail=f"Workspace '{name}' already exists")

    workspace = Workspace(
        id=name,
        owner_user_id=context.user.id,
        name=name,
        description=(getattr(request, "description", None) or "").strip() or None,
        kind="shared",
        is_default=False,
        status="active",
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
    db.commit()
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
    """Update workspace metadata or lifecycle status after permission checks."""
    require_workspace_mutations_enabled()
    workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
    require_workspace_manage(role)
    workspace = lock_workspace_for_update(db, workspace)

    changed = False
    old_name = workspace.name
    cases_to_move: list[Case] = []
    workspace_storage_move = None
    request_name = getattr(request, "name", None)
    request_description = getattr(request, "description", None)
    request_status = getattr(request, "status", None)
    if request_name is not None:
        name = validate_workspace_name_or_400(request_name)
        if name != workspace.name:
            if db.get(Workspace, name) is not None:
                raise HTTPException(status_code=409, detail=f"Workspace '{name}' already exists")
            case_ids = [
                case_id
                for (case_id,) in db.query(Case.id)
                .filter(Case.workspace_id == workspace.id)
                .order_by(Case.id.asc())
                .all()
            ]
            cases_to_move = lock_cases_for_update(db, case_ids)
            ensure_workspace_cases_idle(db, cases_to_move)
            ensure_workspace_runs_idle(db, workspace)
            workspace_storage_move = rewrite_workspace_identity(db, workspace=workspace, cases=cases_to_move, new_workspace_id=name)
        else:
            workspace.name = name
        changed = True

    if request_description is not None:
        workspace.description = request_description.strip() or None
        changed = True

    if request_status is not None:
        new_status = request_status.strip().lower()
        if new_status != "active":
            raise HTTPException(status_code=400, detail="Unsupported workspace status")
        workspace.status = new_status
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="No workspace updates requested")

    try:
        db.commit()
    except Exception:
        db.rollback()
        if workspace_storage_move is not None:
            old_workspace_dir, new_workspace_dir, moved = workspace_storage_move
            rollback_path_move(old_workspace_dir, new_workspace_dir, moved)
        raise
    db.refresh(workspace)
    log_event(db, context, "workspace.updated", details={"workspace_id": workspace.id, "name": workspace.name, "status": workspace.status})
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
    workspace = lock_workspace_for_update(db, workspace)
    if workspace.is_default:
        raise HTTPException(status_code=400, detail="Default workspaces cannot be deleted")

    case_ids = [
        case_id
        for (case_id,) in db.query(Case.id)
        .filter(Case.workspace_id == workspace.id)
        .order_by(Case.id.asc())
        .all()
    ]
    cases = lock_cases_for_update(db, case_ids)
    if cases and not (request and request.confirm_non_empty_delete):
        raise HTTPException(status_code=409, detail="Workspace still contains cases; confirm deletion to delete it")
    ensure_workspace_cases_idle(db, cases)
    ensure_workspace_runs_idle(db, workspace)

    for case in cases:
        purge_case_rows_and_storage(db, case, workspace)

    workspace_artifact_ids = [
        artifact_id
        for (artifact_id,) in db.query(Artifact.id)
        .filter(Artifact.workspace_id == workspace.id, Artifact.case_id.is_(None))
        .all()
    ]
    workspace_thread_ids = [
        thread_id for (thread_id,) in db.query(AssistantThread.id).filter(AssistantThread.workspace_id == workspace.id).all()
    ]
    if workspace_artifact_ids:
        db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(workspace_artifact_ids)).delete(synchronize_session=False)
        db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(workspace_artifact_ids)).delete(synchronize_session=False)
    db.query(CaseEvent).filter(CaseEvent.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(Artifact).filter(Artifact.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(Run).filter(Run.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(AssistantMessage).filter(AssistantMessage.workspace_id == workspace.id).delete(synchronize_session=False)
    if workspace_thread_ids:
        db.query(AssistantCheckpoint).filter(AssistantCheckpoint.thread_id.in_(workspace_thread_ids)).delete(synchronize_session=False)
    db.query(AssistantThread).filter(AssistantThread.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(WorkspaceMembership).filter(WorkspaceMembership.workspace_id == workspace.id).delete(synchronize_session=False)
    delete_workspace_storage(settings, workspace)
    db.delete(workspace)
    db.commit()
    log_event(db, context, "workspace.deleted", details={"deleted_workspace_id": workspace_id})
    return {"deleted": workspace_id}


@router.get("/workspaces/{workspace_id}/batch-runs", response_model=list[WorkspaceBatchRunSummary])
def get_workspace_batch_runs(
    workspace_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> list[WorkspaceBatchRunSummary]:
    """List batch workspace runs for a workspace visible to the user."""
    get_workspace_for_user(db, workspace_id, context.user.id)
    return list_workspace_batch_runs(db, workspace_id)


@router.get("/workspaces/{workspace_id}/batch-runs/{run_id}", response_model=WorkspaceBatchRunDetail)
def get_workspace_batch_run(
    workspace_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> WorkspaceBatchRunDetail:
    """Return detailed state for one workspace batch workspace run."""
    parent_run, _role = get_workspace_batch_run_for_user(db, workspace_id, run_id, context.user.id)
    return serialize_workspace_batch_detail(db, parent_run)


@router.post("/workspaces/{workspace_id}/batch-runs/{run_id}/cancel", response_model=WorkspaceBatchRunDetail)
def cancel_batch_run(
    workspace_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_context),
) -> WorkspaceBatchRunDetail:
    """Cancel a workspace batch workspace run when the user can write to it."""
    require_workspace_mutations_enabled()
    parent_run, role = get_workspace_batch_run_for_user(db, workspace_id, run_id, context.user.id)
    require_workspace_write(role, detail="Insufficient permission to cancel workspace runs")
    detail = cancel_workspace_batch_run(db, parent_run)
    log_event(db, context, "workspace.run_canceled", details={"workspace_id": workspace_id, "run_id": run_id, "run_type": detail.run_type})
    return detail
