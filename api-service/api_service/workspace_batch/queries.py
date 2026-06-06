"""Query and state helpers for workspace batch runs."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend_common.auth import AuthContext
from backend_common.concurrency import lock_cases_for_update
from backend_common.db import (
    Artifact,
    Case,
    Run,
    RunStatus,
    Workspace,
    WorkspaceMembership,
)
from backend_common.run_statuses import ACTIVE_RUN_STATUSES


ACTIVE_CASE_RUN_STATUSES = ACTIVE_RUN_STATUSES


def workspace_case_rows(db: Session, user_id: str, workspace_id: str) -> list[Case]:
    """Return cases visible to a user in a workspace."""
    return (
        db.query(Case)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Case.workspace_id)
        .filter(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
        .order_by(Case.title.asc())
        .all()
    )


def child_runs_for_run(db: Session, run_id: str) -> list[Run]:
    """Return child runs for a parent run in creation order."""
    return (
        db.query(Run)
        .filter(Run.parent_run_id == run_id)
        .order_by(Run.created_at.asc(), Run.id.asc())
        .all()
    )


def artifacts_for_run(db: Session, parent_run: Run) -> list[Artifact]:
    """Return workspace-level artifacts produced by a workspace run."""
    return (
        db.query(Artifact)
        .filter(
            Artifact.workspace_id == parent_run.workspace_id,
            Artifact.case_id.is_(None),
            Artifact.metadata_json["run_id"].as_string() == parent_run.id,
        )
        .order_by(Artifact.created_at.asc())
        .all()
    )


def run_counts(runs: list[Run]) -> dict[str, int]:
    """Count runs by status value."""
    counts = {
        RunStatus.queued.value: 0,
        RunStatus.running.value: 0,
        RunStatus.completed.value: 0,
        RunStatus.failed.value: 0,
        RunStatus.canceled.value: 0,
    }
    for run in runs:
        counts[run.status.value] = counts.get(run.status.value, 0) + 1
    return counts


def report_name_from_run(parent_run: Run) -> str:
    """Return the stored report name or the default batch report name."""
    return str((parent_run.result_json or {}).get("report_name") or "workspace-batch").strip() or "workspace-batch"


def command_from_run(parent_run: Run) -> str:
    """Return the command recorded for a workspace run."""
    return str((parent_run.result_json or {}).get("command") or "").strip()


def analysis_id_from_run(parent_run: Run) -> str:
    """Return the runtime job ID, falling back to the run ID."""
    return str(parent_run.runtime_job_id or parent_run.id)


def selected_case_ids_from_run(parent_run: Run) -> list[str]:
    """Return valid case IDs stored in a run result."""
    stored = (parent_run.result_json or {}).get("case_ids") or []
    return [case_id for case_id in stored if isinstance(case_id, str) and case_id]


def selected_cases_for_run(db: Session, parent_run: Run) -> list[Case]:
    """Return cases in their stored or child-run-derived selection order."""
    case_ids = selected_case_ids_from_run(parent_run)
    if not case_ids:
        case_ids = [run.case_id for run in child_runs_for_run(db, parent_run.id) if run.case_id]
    if not case_ids:
        return []
    case_map = {
        case.id: case
        for case in db.query(Case).filter(Case.id.in_(case_ids)).all()
    }
    return [case_map[case_id] for case_id in case_ids if case_id in case_map]


def select_cases_for_batch(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    case_ids: list[str] | None,
    *,
    require_idle: bool = True,
    lock_selected: bool = False,
) -> list[Case]:
    """Validate and return cases selected for a workspace batch run."""
    available_cases = workspace_case_rows(db, context.user.id, workspace.id)
    if not available_cases:
        raise HTTPException(status_code=404, detail="No cases found in this workspace")

    case_map = {case.id: case for case in available_cases}
    if case_ids:
        selected: list[Case] = []
        for case_id in case_ids:
            case = case_map.get(case_id)
            if case is None:
                raise HTTPException(status_code=404, detail=f"Case not found in workspace: {case_id}")
            selected.append(case)
    else:
        selected = available_cases

    if lock_selected:
        selected_ids = [case.id for case in selected]
        locked_cases = lock_cases_for_update(db, selected_ids)
        locked_case_map = {case.id: case for case in locked_cases}
        selected = [locked_case_map[case_id] for case_id in selected_ids if case_id in locked_case_map]

    if require_idle:
        active_case_ids: list[str] = []
        for case in selected:
            active_run = (
                db.query(Run)
                .filter(Run.case_id == case.id, Run.status.in_(ACTIVE_CASE_RUN_STATUSES))
                .order_by(Run.created_at.desc(), Run.id.desc())
                .first()
            )
            if active_run is not None:
                active_case_ids.append(case.id)
        if active_case_ids:
            joined = ", ".join(active_case_ids)
            raise HTTPException(status_code=409, detail=f"Cases already have active runs: {joined}")

    return selected


def update_parent_run_terminal_state(db: Session, parent_run: Run) -> Run:
    """Set parent run status from the statuses of its child runs."""
    runs = child_runs_for_run(db, parent_run.id)
    counts = run_counts(runs)
    if counts.get(RunStatus.running.value, 0) > 0:
        parent_run.status = RunStatus.running
    elif counts.get(RunStatus.failed.value, 0) > 0:
        parent_run.status = RunStatus.failed
    elif counts.get(RunStatus.queued.value, 0) > 0:
        parent_run.status = RunStatus.running if counts.get(RunStatus.completed.value, 0) > 0 else RunStatus.queued
    elif counts.get(RunStatus.canceled.value, 0) == len(runs):
        parent_run.status = RunStatus.canceled
    else:
        parent_run.status = RunStatus.completed
    return parent_run
