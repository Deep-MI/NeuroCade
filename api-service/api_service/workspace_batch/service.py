"""Provide API service workspace batch behavior for NeuroCade."""

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.artifacts.service import filter_existing_artifacts, serialize_artifact
from api_service.runtime.service import runtime_service
from api_service.runtime import settings
from api_service.runtime.execution import case_artifact_index_target, complete_workspace_run_files, submit_runtime_request
from api_service.schemas import (
    WorkspaceBatchCaseSummary,
    WorkspaceBatchRunDetail,
    WorkspaceBatchRunSummary,
)
from api_service.workspace_batch.filesystem import (
    runtime_visible_data_path as _runtime_visible_data_path,
)
from api_service.workspace_batch.queries import (
    analysis_id_from_run as _analysis_id_from_run,
    child_runs_for_run as _child_runs_for_run,
    artifacts_for_run as _artifacts_for_run,
    command_from_run as _command_from_run,
    report_name_from_run as _report_name_from_run,
    run_counts as _run_counts,
    select_cases_for_batch as _select_cases_for_batch,
    selected_cases_for_run as _selected_cases_for_run,
    workspace_case_rows as _workspace_case_rows,
)
from backend_common.auth import AuthContext
from backend_common.case_storage import case_storage_dir, workspace_analysis_dir
from backend_common.db import (
    AssistantScope,
    Case,
    Run,
    RunStatus,
    SessionLocal,
    Workspace,
)
from backend_common.run_statuses import ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES
from backend_common.runs import (
    WORKSPACE_BATCH_ACTION,
    WORKSPACE_COMMAND_ACTION,
    WORKSPACE_RUN_ACTIONS,
    is_workspace_wide_action,
    workspace_execution_mode,
)
from neurocade_runtime_tools.execution import RuntimeExecutionRequest, RuntimeWorkspaceArtifactSyncTarget

WORKSPACE_BATCH_QUEUE = "workspace_batch"
ACTIVE_CASE_RUN_STATUSES = ACTIVE_RUN_STATUSES
TERMINAL_CASE_RUN_STATUSES = TERMINAL_RUN_STATUSES

_runtime_service = runtime_service


def _serialize_batch_case(db: Session, run: Run) -> WorkspaceBatchCaseSummary:
    """Build the API summary for a single case run in a workspace batch."""
    case = db.get(Case, run.case_id)
    return WorkspaceBatchCaseSummary(
        run_id=run.id,
        case_id=str(run.case_id or ""),
        case_title=case.title if case is not None else str(run.case_id or ""),
        status=run.status.value,
        external_task_id=run.external_task_id,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def serialize_workspace_batch_run(db: Session, parent_run: Run) -> WorkspaceBatchRunSummary:
    """Summarize a workspace run with case counts and artifact metadata."""
    runs = _child_runs_for_run(db, parent_run.id)
    selected_case_count = len(_selected_cases_for_run(db, parent_run))
    artifacts = filter_existing_artifacts(_artifacts_for_run(db, parent_run))
    counts = _run_counts(runs)
    result_json = parent_run.result_json or {}
    return WorkspaceBatchRunSummary(
        run_id=parent_run.id,
        workspace_id=parent_run.workspace_id,
        status=parent_run.status.value,
        run_type=parent_run.run_type,
        execution_mode=workspace_execution_mode(parent_run.run_type),
        command=_command_from_run(parent_run),
        report_name=_report_name_from_run(parent_run),
        analysis_id=_analysis_id_from_run(parent_run),
        selected_case_count=selected_case_count,
        total_cases=len(runs) if runs else selected_case_count,
        queued_cases=counts.get(RunStatus.queued.value, 0),
        running_cases=counts.get(RunStatus.running.value, 0),
        completed_cases=counts.get(RunStatus.completed.value, 0),
        failed_cases=counts.get(RunStatus.failed.value, 0),
        canceled_cases=counts.get(RunStatus.canceled.value, 0),
        external_task_id=str(result_json.get("external_task_id") or "") or None,
        artifact_count=len(artifacts),
        created_at=parent_run.created_at,
        updated_at=parent_run.updated_at,
    )


def serialize_workspace_batch_detail(db: Session, parent_run: Run) -> WorkspaceBatchRunDetail:
    """Return full workspace run details including selected cases and artifacts."""
    summary = serialize_workspace_batch_run(db, parent_run)
    runs = _child_runs_for_run(db, parent_run.id)
    selected_cases = _selected_cases_for_run(db, parent_run)
    artifacts = [
        serialize_artifact(artifact)
        for artifact in filter_existing_artifacts(_artifacts_for_run(db, parent_run))
    ]
    if runs:
        cases = [_serialize_batch_case(db, run) for run in runs]
    else:
        cases = [
            WorkspaceBatchCaseSummary(
                run_id=f"{parent_run.id}:{case.id}",
                case_id=case.id,
                case_title=case.title,
                status=parent_run.status.value,
                external_task_id=str((parent_run.result_json or {}).get("external_task_id") or "") or None,
                error_message=None,
                created_at=parent_run.created_at,
                updated_at=parent_run.updated_at,
            )
            for case in selected_cases
        ]
    return WorkspaceBatchRunDetail(
        **summary.model_dump(),
        cases=cases,
        artifacts=artifacts,
    )


def list_workspace_batch_runs(db: Session, workspace_id: str) -> list[WorkspaceBatchRunSummary]:
    """List workspace batch and command runs for a workspace, newest first."""
    rows = (
        db.query(Run)
        .filter(
            Run.workspace_id == workspace_id,
            Run.scope_type == AssistantScope.workspace,
            Run.run_type.in_(WORKSPACE_RUN_ACTIONS),
        )
        .order_by(Run.created_at.desc())
        .all()
    )
    return [serialize_workspace_batch_run(db, row) for row in rows]


def get_workspace_batch_run_or_404(db: Session, workspace_id: str, run_id: str) -> Run:
    """Fetch a workspace run or raise a 404 when it is unavailable."""
    parent_run = (
        db.query(Run)
        .filter(
            Run.workspace_id == workspace_id,
            Run.id == run_id,
            Run.scope_type == AssistantScope.workspace,
            Run.run_type.in_(WORKSPACE_RUN_ACTIONS),
        )
        .one_or_none()
    )
    if parent_run is None:
        raise HTTPException(status_code=404, detail="Workspace run not found")
    return parent_run


def queue_workspace_batch_case(run_id: str, case_id: str, *, is_probe: bool) -> str:
    """Queue a background job for one case in a workspace batch run."""
    from api_service.workspace_batch.tasks import EXECUTE_WORKSPACE_BATCH_CASE_TASK

    user_id: str | None = None
    workspace_id: str | None = None
    artifact_index_targets = ()
    with SessionLocal() as lookup_db:
        parent_run = (
            lookup_db.query(Run)
            .filter(Run.id == run_id)
            .one_or_none()
        )
        case = lookup_db.get(Case, case_id)
        if parent_run is not None:
            user_id = parent_run.created_by_user_id
            workspace_id = parent_run.workspace_id
        if case is not None:
            artifact_index_targets = (case_artifact_index_target(case),)

    # Pre-generate the job id so the runner can persist it as external_task_id.
    task_id = str(uuid4())
    submission = submit_runtime_request(
        EXECUTE_WORKSPACE_BATCH_CASE_TASK,
        RuntimeExecutionRequest(
            argv=[EXECUTE_WORKSPACE_BATCH_CASE_TASK],
            execution_mode="job-submit",
            synchronous=False,
            queue_name=WORKSPACE_BATCH_QUEUE,
            task_id=task_id,
            user_id=user_id,
            workspace_id=workspace_id,
            case_id=case_id,
            artifact_index_targets=artifact_index_targets,
        ),
        kwargs={"run_id": run_id, "case_id": case_id, "task_id": task_id, "is_probe": is_probe},
    )
    return submission.submitted_task_id or ""


def _new_workspace_run(
    context: AuthContext,
    workspace: Workspace,
    *,
    run_type: str,
    command: str,
    report_name: str | None,
    default_report_name: str,
    selected_cases: list[Case],
    thread_id: str | None,
    provider_name: str,
    model_name: str,
) -> Run:
    """Build the shared parent run row for workspace batch and command runs."""
    run_id = f"run-{uuid4().hex[:12]}"
    analysis_id = f"ws-analysis-{uuid4().hex[:12]}"
    return Run(
        id=run_id,
        scope_type=AssistantScope.workspace,
        case_id=None,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        assistant_thread_id=None,
        status=RunStatus.queued,
        run_type=run_type,
        thread_id=thread_id or f"workspace:{workspace.id}",
        provider_name=provider_name,
        model_name=model_name,
        runtime_job_id=analysis_id,
        result_json={
            "command": command,
            "report_name": (report_name or default_report_name).strip() or default_report_name,
            "case_ids": [case.id for case in selected_cases],
            "analysis_id": analysis_id,
        },
    )


def create_workspace_batch_run(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    command: str,
    report_name: str | None,
    case_ids: list[str] | None = None,
    thread_id: str | None = None,
    provider_name: str,
    model_name: str,
) -> WorkspaceBatchRunSummary:
    """Create a case-by-case workspace batch run and queue its probe case."""
    normalized_command = command.strip()
    if not normalized_command:
        raise HTTPException(status_code=400, detail="Batch command cannot be empty")

    selected_cases = _select_cases_for_batch(db, context, workspace, case_ids, lock_selected=True)
    parent_run = _new_workspace_run(
        context,
        workspace,
        run_type=WORKSPACE_BATCH_ACTION,
        command=normalized_command,
        report_name=report_name,
        default_report_name="workspace-batch",
        selected_cases=selected_cases,
        thread_id=thread_id,
        provider_name=provider_name,
        model_name=model_name,
    )
    try:
        db.add(parent_run)
        db.flush()

        created_runs: list[Run] = []
        for index, case in enumerate(selected_cases):
            run = Run(
                parent_run_id=parent_run.id,
                scope_type=AssistantScope.case,
                case_id=case.id,
                workspace_id=workspace.id,
                created_by_user_id=context.user.id,
                status=RunStatus.queued,
                runtime_job_id=case.id,
                run_type=WORKSPACE_BATCH_ACTION,
                input_json={
                    "command": normalized_command,
                    "report_name": _report_name_from_run(parent_run),
                    "batch_index": index,
                    "is_probe": index == 0,
                },
                result_json={},
            )
            db.add(run)
            created_runs.append(run)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="One or more selected cases already have active runs") from exc

    complete_workspace_run_files(db, parent_run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Workspace batch run conflicts with another update. Please retry.") from exc
    db.refresh(parent_run)

    try:
        probe_task_id = queue_workspace_batch_case(parent_run.id, str(created_runs[0].case_id), is_probe=True)
    except Exception as exc:
        created_runs[0].status = RunStatus.failed
        created_runs[0].error_message = f"Failed to queue probe case: {exc}"
        for pending_run in created_runs[1:]:
            pending_run.status = RunStatus.canceled
            pending_run.error_message = "Canceled because the probe case failed to queue."
        parent_run.status = RunStatus.failed
        complete_workspace_run_files(db, parent_run)
        db.commit()
        db.refresh(parent_run)
        return serialize_workspace_batch_run(db, parent_run)

    created_runs[0].external_task_id = probe_task_id
    complete_workspace_run_files(db, parent_run)
    db.commit()
    db.refresh(parent_run)
    return serialize_workspace_batch_run(db, parent_run)


async def workspace_probe_bash(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    command: str,
    case_id: str | None = None,
) -> str:
    """Run a bash command against one workspace case and return its output text."""
    if case_id:
        selected_cases = _select_cases_for_batch(db, context, workspace, [case_id])
    else:
        available_cases = _workspace_case_rows(db, context.user.id, workspace.id)
        if not available_cases:
            raise HTTPException(status_code=404, detail="No cases found in this workspace")
        target_case = available_cases[0]
        selected_cases = _select_cases_for_batch(db, context, workspace, [target_case.id])
    target_case = selected_cases[0]
    result = await _runtime_service.run_workspace_case_command(
        command=command,
        case_dir=_runtime_visible_data_path(
            case_storage_dir(settings, workspace.id, target_case.id)
        ),
        db=db,
        artifact_index_targets=(case_artifact_index_target(target_case),),
    )
    return (
        f"Workspace probe command ran on case `{target_case.title}` ({target_case.id}).\n"
        f"The case directory was mounted at `/case`.\n\n{result}"
    )


def queue_workspace_command_run(run_id: str) -> str:
    """Queue the background job that executes a workspace-wide command run."""
    from api_service.workspace_batch.tasks import EXECUTE_WORKSPACE_COMMAND_TASK

    user_id: str | None = None
    workspace_id: str | None = None
    workspace_artifact_sync_targets = ()
    with SessionLocal() as lookup_db:
        parent_run = (
            lookup_db.query(Run)
            .filter(Run.id == run_id)
            .one_or_none()
        )
        if parent_run is not None:
            user_id = parent_run.created_by_user_id
            workspace_id = parent_run.workspace_id
            analysis_id = _analysis_id_from_run(parent_run)
            workspace_artifact_sync_targets = (
                RuntimeWorkspaceArtifactSyncTarget(
                    run_id=run_id,
                    analysis_dir=workspace_analysis_dir(settings, parent_run.workspace_id, analysis_id),
                ),
            )

    # Pre-generate the job id so the runner can persist it as external_task_id.
    task_id = str(uuid4())
    submission = submit_runtime_request(
        EXECUTE_WORKSPACE_COMMAND_TASK,
        RuntimeExecutionRequest(
            argv=[EXECUTE_WORKSPACE_COMMAND_TASK],
            execution_mode="job-submit",
            synchronous=False,
            queue_name=WORKSPACE_BATCH_QUEUE,
            task_id=task_id,
            user_id=user_id,
            workspace_id=workspace_id,
            workspace_artifact_sync_targets=workspace_artifact_sync_targets,
        ),
        kwargs={"run_id": run_id, "task_id": task_id},
    )
    return submission.submitted_task_id or ""


def create_workspace_command_run(
    db: Session,
    context: AuthContext,
    workspace: Workspace,
    *,
    command: str,
    report_name: str | None,
    case_ids: list[str] | None = None,
    thread_id: str | None = None,
    provider_name: str,
    model_name: str,
) -> WorkspaceBatchRunSummary:
    """Create and queue a workspace-wide command run for selected cases."""
    normalized_command = command.strip()
    if not normalized_command:
        raise HTTPException(status_code=400, detail="Workspace command cannot be empty")

    selected_cases = _select_cases_for_batch(db, context, workspace, case_ids)
    parent_run = _new_workspace_run(
        context,
        workspace,
        run_type=WORKSPACE_COMMAND_ACTION,
        command=normalized_command,
        report_name=report_name,
        default_report_name="workspace-command",
        selected_cases=selected_cases,
        thread_id=thread_id,
        provider_name=provider_name,
        model_name=model_name,
    )
    db.add(parent_run)
    complete_workspace_run_files(db, parent_run)
    db.commit()
    db.refresh(parent_run)

    try:
        task_id = queue_workspace_command_run(parent_run.id)
    except Exception as exc:
        parent_run.status = RunStatus.failed
        parent_run.result_json = {
            **(parent_run.result_json or {}),
            "queue_error": str(exc),
        }
        complete_workspace_run_files(db, parent_run)
        db.commit()
        db.refresh(parent_run)
        return serialize_workspace_batch_run(db, parent_run)

    parent_run.result_json = {
        **(parent_run.result_json or {}),
        "external_task_id": task_id,
    }
    complete_workspace_run_files(db, parent_run)
    db.commit()
    db.refresh(parent_run)
    return serialize_workspace_batch_run(db, parent_run)


def cancel_workspace_batch_run(db: Session, parent_run: Run) -> WorkspaceBatchRunDetail:
    """Cancel active jobs for a workspace run and return updated details."""
    from api_service.jobs import job_manager

    runs = _child_runs_for_run(db, parent_run.id)
    if is_workspace_wide_action(parent_run.run_type):
        task_id = str((parent_run.result_json or {}).get("external_task_id") or "").strip()
        if task_id:
            job_manager.cancel(task_id)
    else:
        for run in runs:
            if run.status not in TERMINAL_CASE_RUN_STATUSES:
                if run.external_task_id:
                    job_manager.cancel(run.external_task_id)
                run.status = RunStatus.canceled
                run.error_message = "Canceled by user."

    parent_run.status = RunStatus.canceled
    parent_run.result_json = {
        **(parent_run.result_json or {}),
        "canceled_by_user": True,
    }
    complete_workspace_run_files(db, parent_run)
    db.commit()
    db.refresh(parent_run)
    return serialize_workspace_batch_detail(db, parent_run)
