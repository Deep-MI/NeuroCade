"""Workspace batch and workspace-wide command execution runners."""

from __future__ import annotations

import asyncio

from neurocade_runtime_tools.execution import RuntimeCompletionHooks
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.runtime.execution import (
    case_artifact_index_target,
    case_log_artifact_target,
    complete_runtime_request,
    complete_workspace_run_files,
)
from api_service.runtime.service import runtime_service
from api_service.workspace_batch import WORKSPACE_BATCH_QUEUE, queue_workspace_batch_case
from api_service.workspace_batch.filesystem import prepare_workspace_command_inputs as _prepare_workspace_command_inputs
from api_service.workspace_batch.filesystem import runtime_visible_data_path as _runtime_visible_data_path
from api_service.workspace_batch.queries import (
    analysis_id_from_run as _analysis_id_from_run,
)
from api_service.workspace_batch.queries import (
    child_runs_for_run as _child_runs_for_run,
)
from api_service.workspace_batch.queries import (
    command_from_run as _command_from_run,
)
from api_service.workspace_batch.queries import (
    update_parent_run_terminal_state as _update_parent_run_terminal_state,
)
from api_service.workspace_batch.reports import result_text_has_execution_error as _result_text_has_execution_error
from api_service.workspace_batch.reports import write_case_log as _write_case_log
from backend_common.case_storage import case_storage_dir
from backend_common.db import Case, Run, RunStatus, Workspace
from backend_common.run_statuses import TERMINAL_RUN_STATUSES as TERMINAL_CASE_RUN_STATUSES
from backend_common.runs import WORKSPACE_BATCH_ACTION, WORKSPACE_COMMAND_ACTION

_runtime_service = runtime_service


def _case_run_was_canceled(db: Session, parent_run: Run, run: Run) -> bool:
    """Return whether the parent run or case run has been canceled in the database."""
    db.refresh(parent_run)
    db.refresh(run)
    return parent_run.status == RunStatus.canceled or run.status == RunStatus.canceled


def process_workspace_batch_case(run_id: str, case_id: str, *, task_id: str, is_probe: bool) -> None:
    """Execute a workspace batch command for one case and update run state."""
    from backend_common.db import SessionLocal

    with SessionLocal() as db:
        parent_run = (
            db.query(Run)
            .filter(Run.id == run_id, Run.run_type == WORKSPACE_BATCH_ACTION)
            .one_or_none()
        )
        if parent_run is None:
            return
        if parent_run.status == RunStatus.canceled:
            return

        run = (
            db.query(Run)
            .filter(Run.parent_run_id == run_id, Run.case_id == case_id)
            .one_or_none()
        )
        case = db.get(Case, case_id)
        workspace = db.get(Workspace, parent_run.workspace_id)
        if run is None or case is None or workspace is None:
            return
        if run.status == RunStatus.canceled:
            return

        command = str(run.input_json.get("command") or _command_from_run(parent_run)).strip()
        run.external_task_id = task_id
        run.status = RunStatus.running
        parent_run.status = RunStatus.running
        complete_workspace_run_files(db, parent_run)
        db.commit()

        try:
            result = asyncio.run(
                _runtime_service.run_workspace_case_command(
                    command=command,
                    case_dir=_runtime_visible_data_path(
                        case_storage_dir(settings, workspace.id, case.id)
                    ),
                    db=db,
                    artifact_index_targets=(case_artifact_index_target(case),),
                    queue_name=WORKSPACE_BATCH_QUEUE,
                    task_id=task_id,
                )
            )
            if _case_run_was_canceled(db, parent_run, run):
                return
            if _result_text_has_execution_error(result):
                raise RuntimeError(result)
            log_text = f"$ {command}\n\n{result}\n"
            log_path = _write_case_log(workspace, case, run_id, log_text)
            complete_runtime_request(
                db,
                RuntimeCompletionHooks(
                    case_log_artifact_targets=(
                        case_log_artifact_target(
                            workspace_id=workspace.id,
                            case_id=case.id,
                            run_id=run_id,
                            log_path=log_path,
                            run_type=WORKSPACE_BATCH_ACTION,
                        ),
                    )
                ),
            )
            run.status = RunStatus.completed
            run.error_message = None
            run.result_json = {
                **(run.result_json or {}),
                "result_excerpt": result[:4000],
            }
            if is_probe:
                remaining_runs = [
                    pending_run
                    for pending_run in _child_runs_for_run(db, run_id)
                    if pending_run.case_id != case_id and pending_run.external_task_id is None and pending_run.status == RunStatus.queued
                ]
                for pending_run in remaining_runs:
                    pending_run.external_task_id = queue_workspace_batch_case(
                        run_id,
                        str(pending_run.case_id),
                        is_probe=False,
                    )
        except Exception as exc:
            if _case_run_was_canceled(db, parent_run, run):
                return
            error_message = str(exc)
            log_path = _write_case_log(workspace, case, run_id, f"$ {command}\n\nERROR: {error_message}\n")
            complete_runtime_request(
                db,
                RuntimeCompletionHooks(
                    case_log_artifact_targets=(
                        case_log_artifact_target(
                            workspace_id=workspace.id,
                            case_id=case.id,
                            run_id=run_id,
                            log_path=log_path,
                            run_type=WORKSPACE_BATCH_ACTION,
                        ),
                    )
                ),
            )
            run.status = RunStatus.failed
            run.error_message = error_message
            run.result_json = {
                **(run.result_json or {}),
                "result_excerpt": error_message[:4000],
            }
            if is_probe:
                for pending_run in _child_runs_for_run(db, run_id):
                    if pending_run.case_id == case_id or pending_run.status in TERMINAL_CASE_RUN_STATUSES:
                        continue
                    if pending_run.external_task_id:
                        from api_service.jobs import job_manager

                        job_manager.cancel(pending_run.external_task_id)
                    pending_run.status = RunStatus.canceled
                    pending_run.error_message = "Canceled after the probe case failed."

        _update_parent_run_terminal_state(db, parent_run)
        complete_workspace_run_files(db, parent_run)
        db.commit()


def process_workspace_command_run(run_id: str, *, task_id: str) -> None:
    """Execute a workspace-wide command and persist its status and log output."""
    from backend_common.db import SessionLocal

    with SessionLocal() as db:
        parent_run = (
            db.query(Run)
            .filter(Run.id == run_id, Run.run_type == WORKSPACE_COMMAND_ACTION)
            .one_or_none()
        )
        if parent_run is None or parent_run.status == RunStatus.canceled:
            return

        workspace = db.get(Workspace, parent_run.workspace_id)
        if workspace is None:
            return

        command = _command_from_run(parent_run)
        analysis_id = _analysis_id_from_run(parent_run)
        analysis_dir, cases_dir = _prepare_workspace_command_inputs(
            db,
            parent_run,
            workspace,
            analysis_id=analysis_id,
        )
        parent_run.status = RunStatus.running
        parent_run.result_json = {
            **(parent_run.result_json or {}),
            "external_task_id": task_id,
        }
        complete_workspace_run_files(db, parent_run)
        db.commit()

        command_log_path = analysis_dir / "command.log"
        try:
            result = asyncio.run(
                _runtime_service.run_workspace_command(
                    command=command,
                    cases_dir=_runtime_visible_data_path(cases_dir),
                    workspace_dir=_runtime_visible_data_path(analysis_dir),
                    db=db,
                    queue_name=WORKSPACE_BATCH_QUEUE,
                    task_id=task_id,
                )
            )
            if _result_text_has_execution_error(result):
                raise RuntimeError(result)
            db.refresh(parent_run)
            if parent_run.status == RunStatus.canceled:
                return
            command_log_path.write_text(f"$ {command}\n\n{result}\n", encoding="utf-8")
            parent_run.status = RunStatus.completed
            parent_run.result_json = {
                **(parent_run.result_json or {}),
                "result_excerpt": result[:4000],
            }
        except Exception as exc:
            db.refresh(parent_run)
            if parent_run.status == RunStatus.canceled:
                return
            error_message = str(exc)
            command_log_path.write_text(f"$ {command}\n\nERROR: {error_message}\n", encoding="utf-8")
            parent_run.status = RunStatus.failed
            parent_run.result_json = {
                **(parent_run.result_json or {}),
                "result_excerpt": error_message[:4000],
            }

        complete_workspace_run_files(db, parent_run)
        db.commit()
