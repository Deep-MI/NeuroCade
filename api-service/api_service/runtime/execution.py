"""API-service runtime execution wrapper and completion hooks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from api_service.runtime import settings
from backend_common.db import Case, Run, Workspace
from backend_common.scan import index_case_files_from_storage
from neurocade_runtime_tools.execution import (
    RuntimeArtifactIndexTarget,
    RuntimeCaseLogArtifactTarget,
    RuntimeCompletionHooks,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeWorkspaceArtifactSyncTarget,
    execute_runtime_request as _execute_runtime_request,
)

logger = logging.getLogger(__name__)


def case_artifact_index_target(case: Case) -> RuntimeArtifactIndexTarget:
    """Build the runtime artifact indexing target for a case."""
    return RuntimeArtifactIndexTarget(
        user_id=case.owner_user_id,
        workspace_id=case.workspace_id,
        case_id=case.id,
        case_title=case.title,
    )


def case_log_artifact_target(
    *,
    workspace_id: str,
    case_id: str,
    run_id: str,
    log_path: Path | str,
    run_type: str,
) -> RuntimeCaseLogArtifactTarget:
    """Build a runtime completion target for a per-case log artifact."""
    return RuntimeCaseLogArtifactTarget(
        workspace_id=workspace_id,
        case_id=case_id,
        run_id=run_id,
        log_path=log_path,
        run_type=run_type,
    )


def workspace_artifact_sync_target(*, run_id: str, analysis_dir: Path | str) -> RuntimeWorkspaceArtifactSyncTarget:
    """Build a runtime completion target for workspace analysis artifacts."""
    return RuntimeWorkspaceArtifactSyncTarget(
        run_id=run_id,
        analysis_dir=analysis_dir,
    )


def run_runtime_completion_hooks(db: Session, request: RuntimeExecutionRequest | RuntimeCompletionHooks) -> None:
    """Run shared post-execution hooks described by a runtime request."""
    for target in request.artifact_index_targets:
        try:
            index_case_files_from_storage(
                db,
                settings,
                target.user_id,
                target.case_id,
                target.workspace_id,
                case_title=target.case_title,
                preferred_upload_name=target.preferred_upload_name,
            )
        except Exception:
            logger.exception(
                "runtime_execution.artifact_index_failed workspace_id=%s case_id=%s",
                target.workspace_id,
                target.case_id,
            )
            raise
    for target in request.case_log_artifact_targets:
        _sync_case_log_artifact(db, target)
    for target in request.workspace_artifact_sync_targets:
        _sync_workspace_artifacts(db, target)


def complete_runtime_request(db: Session, request: RuntimeExecutionRequest | RuntimeCompletionHooks) -> None:
    """Complete API-service runtime metadata after execution finishes."""
    run_runtime_completion_hooks(db, request)


def complete_workspace_run_files(db: Session, parent_run: Run) -> Path:
    """Write workspace run files and sync them through runtime completion."""
    from api_service.workspace_batch.reports import write_run_files

    analysis_dir = write_run_files(db, parent_run)
    db.flush()
    complete_runtime_request(
        db,
        RuntimeCompletionHooks(
            workspace_artifact_sync_targets=(
                workspace_artifact_sync_target(
                    run_id=parent_run.id,
                    analysis_dir=analysis_dir,
                ),
            )
        ),
    )
    return analysis_dir


def runtime_completion_has_targets(request: RuntimeExecutionRequest | RuntimeCompletionHooks | None) -> bool:
    """Return whether a runtime completion object has work to do."""
    if request is None:
        return False
    return bool(
        request.artifact_index_targets
        or request.case_log_artifact_targets
        or request.workspace_artifact_sync_targets
    )


class RuntimeCompletionGuard:
    """Run runtime completion once for worker code paths with multiple exits."""

    def __init__(self, db_factory: Any) -> None:
        self.db_factory = db_factory
        self.completed = False

    def complete(self, request: RuntimeExecutionRequest | RuntimeCompletionHooks | None) -> bool:
        """Complete runtime metadata once, returning whether hooks ran."""
        if self.completed or not runtime_completion_has_targets(request):
            return False
        assert request is not None
        self.completed = True
        with self.db_factory() as db:
            complete_runtime_request(db, request)
            db.commit()
        return True


def _sync_case_log_artifact(db: Session, target: RuntimeCaseLogArtifactTarget) -> None:
    from api_service.workspace_batch.reports import ensure_case_log_artifact

    workspace = db.get(Workspace, target.workspace_id)
    case = db.get(Case, target.case_id)
    if workspace is None:
        raise ValueError(f"Workspace {target.workspace_id} not found for case log artifact")
    if case is None:
        raise ValueError(f"Case {target.case_id} not found for case log artifact")
    ensure_case_log_artifact(
        db,
        workspace,
        case,
        target.run_id,
        Path(target.log_path),
        run_type=target.run_type,
    )


def _sync_workspace_artifacts(db: Session, target: RuntimeWorkspaceArtifactSyncTarget) -> None:
    from api_service.workspace_batch.reports import sync_workspace_analysis_artifacts

    parent_run = (
        db.query(Run)
        .filter(Run.id == target.run_id)
        .one_or_none()
    )
    if parent_run is None:
        raise ValueError(f"Run {target.run_id} not found for workspace artifact sync")
    sync_workspace_analysis_artifacts(db, parent_run, Path(target.analysis_dir))


def execute_runtime_request(
    request: RuntimeExecutionRequest,
    *,
    db: Session | None = None,
    run_completion_hooks: bool = True,
) -> RuntimeExecutionResult:
    """Execute a runtime request and run API-service completion hooks."""
    try:
        result = _execute_runtime_request(request)
    except TimeoutError:
        if db is not None and run_completion_hooks:
            complete_runtime_request(db, request)
        raise
    if db is not None and run_completion_hooks:
        complete_runtime_request(db, request)
    return result


def submit_runtime_request(
    celery_task: Any,
    request: RuntimeExecutionRequest,
    *,
    kwargs: dict[str, Any] | None = None,
) -> RuntimeExecutionResult:
    """Submit an asynchronous runtime request through an API-service Celery task."""
    if request.synchronous:
        raise ValueError("Runtime task submission requires request.synchronous=False")
    submit_kwargs: dict[str, Any] = {
        "kwargs": dict(kwargs or {}),
    }
    if request.queue_name:
        submit_kwargs["queue"] = request.queue_name
    if request.task_id:
        submit_kwargs["task_id"] = request.task_id

    logger.info(
        "runtime_execution.submit mode=%s queue=%s task_id=%s user_id=%s workspace_id=%s case_id=%s command=%s",
        request.execution_mode,
        request.queue_name,
        request.task_id,
        request.user_id,
        request.workspace_id,
        request.case_id,
        request.command,
    )
    async_result = celery_task.apply_async(**submit_kwargs)
    request.task_id = str(async_result.id)
    return RuntimeExecutionResult(
        request=request,
        returncode=0,
        logs=list(request.log_lines),
        execution_backend="celery-submit",
        submitted_task_id=request.task_id,
    )
