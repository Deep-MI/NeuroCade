"""Shared durable lifecycle operations for catalog workflow runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from api_service.jobs import job_manager
from api_service.runtime.neuroimaging_tasks import submit_neuroimaging_workflow
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow
from backend_common.db import Run, RunStatus, run_with_sqlite_lock_retry


def workflow_run_snapshot(workflow: NeuroimagingWorkflow, *, gpu_enabled: bool) -> dict[str, Any]:
    """Return the immutable workflow definition and resolved execution device."""
    return {
        "workflow_definition": workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        "execution": {"device": "cuda" if gpu_enabled else "cpu"},
    }


def workflow_execution_details(workflow: NeuroimagingWorkflow, *, gpu_enabled: bool) -> dict[str, Any]:
    """Return the execution metadata exposed in assistant tool results."""
    return {
        "image": workflow.image,
        "mode": workflow.execution.mode,
        "gpu": gpu_enabled,
        "timeout_s": workflow.execution.timeout_s,
    }


def submit_workflow_run(
    run: Run,
    workflow: NeuroimagingWorkflow,
    inputs: list[str],
    *,
    bind_host_path: Path,
    bind_container_path: str,
    job_id: str,
    gpu_enabled: bool,
) -> None:
    """Submit a persisted run and verify that the worker kept its durable job ID."""
    submitted_job_id = submit_neuroimaging_workflow(
        run=run,
        workflow=workflow,
        inputs=inputs,
        bind_host_path=bind_host_path,
        bind_container_path=bind_container_path,
        job_id=job_id,
        gpu_enabled=gpu_enabled,
    )
    if submitted_job_id != job_id:
        raise RuntimeError("Background worker returned an unexpected job id")


def mark_workflow_run_failed(db: Session, run_id: str, tool_id: str, error: Exception | str) -> Run | None:
    """Persist a submission failure for a run that was already queued."""
    run = db.get(Run, run_id)
    if run is None:
        return None
    message = str(error)
    run.status = RunStatus.failed
    run.error_message = message
    run.result_json = {"status": "failed", "run_id": run.id, "tool_id": tool_id}
    db.commit()
    return run


def cancel_workflow_run(db: Session, run: Run, *, cancel_job_first: bool = False) -> Run:
    """Cancel a run while preserving the caller's established worker ordering."""
    run_id = run.id
    job_id = run.job_id
    tool_id = run.run_type

    def mark_canceled() -> Run:
        current = db.get(Run, run_id)
        if current is None:
            raise ValueError(f"Workflow run {run_id!r} no longer exists")
        current.status = RunStatus.canceled
        current.error_message = None
        current.result_json = {"status": "canceled", "tool_id": tool_id, "run_id": run_id}
        db.commit()
        return current

    if cancel_job_first and job_id:
        job_manager.cancel(job_id)
    canceled = run_with_sqlite_lock_retry(db, mark_canceled)
    if not cancel_job_first and job_id:
        job_manager.cancel(job_id)
    return canceled
