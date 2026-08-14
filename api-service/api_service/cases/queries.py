"""Read-only case queries and response assembly."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from api_service.cases.serializers import serialize_case_detail, serialize_case_summary, serialize_run_summary
from api_service.cases.service import latest_case_run
from api_service.helpers import get_case_for_user, get_workspace_for_user, log_event
from api_service.policies import require_case_read
from api_service.runtime import settings
from api_service.schemas import CaseDetail, CaseSummary, RunSummary
from backend_common.artifact_reconciliation import reconcile_artifacts
from backend_common.auth import AuthContext
from backend_common.case_storage import resolve_case_storage
from backend_common.db import Artifact, AssistantScope, AssistantThread, Case, Run, Workspace, WorkspaceMembership
from backend_common.run_logs import render_run_logs


def list_visible_cases(
    db: Session,
    context: AuthContext,
    *,
    workspace_id: str | None = None,
) -> list[CaseSummary]:
    """List cases visible to the current user, optionally scoped to a workspace."""
    if workspace_id:
        get_workspace_for_user(db, workspace_id, context.user.id)
    query = (
        db.query(Case, Workspace, WorkspaceMembership.role)
        .join(Workspace, Workspace.id == Case.workspace_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.workspace_id == Case.workspace_id) & (WorkspaceMembership.user_id == context.user.id),
        )
    )
    if workspace_id:
        query = query.filter(Case.workspace_id == workspace_id)

    case_rows = []
    for case, workspace, role in query.all():
        try:
            resolve_case_storage(settings, case, workspace)
        except FileNotFoundError:
            continue
        case_rows.append((case, role))
    case_ids = [case.id for case, _role in case_rows]
    threads_by_case: dict[str, AssistantThread] = {}
    runs_by_case: dict[str, Run] = {}
    artifact_counts: dict[str, int] = {}
    if case_ids:
        threads_by_case = {
            str(thread.case_id): thread
            for thread in db.query(AssistantThread)
            .filter(
                AssistantThread.scope_type == AssistantScope.case,
                AssistantThread.case_id.in_(case_ids),
                AssistantThread.created_by_user_id == context.user.id,
            )
            .all()
            if thread.case_id is not None
        }
        ranked_runs = (
            db.query(
                Run.id.label("run_id"),
                func.row_number()
                .over(
                    partition_by=Run.case_id,
                    order_by=(Run.created_at.desc(), Run.id.desc()),
                )
                .label("position"),
            )
            .filter(Run.case_id.in_(case_ids))
            .subquery()
        )
        for run in (
            db.query(Run)
            .join(ranked_runs, ranked_runs.c.run_id == Run.id)
            .filter(ranked_runs.c.position == 1)
            .all()
        ):
            if run.case_id is not None:
                runs_by_case.setdefault(run.case_id, run)
        for artifact in reconcile_artifacts(
            db,
            db.query(Artifact).filter(Artifact.case_id.in_(case_ids)).all()
        ):
            if artifact.case_id is not None:
                artifact_counts[artifact.case_id] = artifact_counts.get(artifact.case_id, 0) + 1

    summaries = [
        serialize_case_summary(
            case,
            role,
            thread=threads_by_case.get(case.id),
            latest_run=runs_by_case.get(case.id),
            artifact_count=artifact_counts.get(case.id, 0),
        )
        for case, role in case_rows
    ]
    db.commit()
    return summaries


def get_case_detail_for_user(db: Session, context: AuthContext, *, case_id: str) -> CaseDetail:
    """Return case metadata, artifacts, runs, and assistant thread details."""
    case, _workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    thread = (
        db.query(AssistantThread)
        .filter(
            AssistantThread.scope_type == AssistantScope.case,
            AssistantThread.case_id == case.id,
            AssistantThread.created_by_user_id == context.user.id,
        )
        .one_or_none()
    )
    artifacts = db.query(Artifact).filter(Artifact.case_id == case.id).order_by(Artifact.created_at.desc()).all()
    runs = db.query(Run).filter(Run.case_id == case.id).order_by(Run.created_at.desc()).all()
    log_event(db, context, "case.viewed", case_id=case_id)
    return serialize_case_detail(
        case,
        role,
        thread=thread,
        artifacts=reconcile_artifacts(db, artifacts),
        runs=runs,
    )


def list_case_runs_for_user(db: Session, context: AuthContext, *, case_id: str) -> list[RunSummary]:
    """List durable run records for a case without mutating state."""
    _case, _workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    runs = db.query(Run).filter(Run.case_id == case_id).order_by(Run.created_at.desc()).all()
    return [serialize_run_summary(run) for run in runs]


def get_case_logs_for_user(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Return live runtime logs or stored logs for the latest case run."""
    case, _workspace, role, case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    latest_run = latest_case_run(db, case.id)
    if latest_run is None:
        return {"logs": ""}
    return {"logs": render_run_logs(case_dir, latest_run.id)}
