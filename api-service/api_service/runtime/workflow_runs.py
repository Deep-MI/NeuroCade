"""Shared durable lifecycle operations for catalog workflow runs."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from neurocade_runtime_tools.bridge_client import BridgeClient
from sqlalchemy.orm import Session

from api_service.jobs import job_manager
from api_service.runtime.neuroimaging_tasks import submit_neuroimaging_workflow
from api_service.runtime.run_admission import guard_output_submission
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow
from backend_common.db import Run, RunStatus, run_with_sqlite_lock_retry
from backend_common.run_statuses import run_is_active


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




@guard_output_submission
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
    from api_service.runtime_tools.errors import workflow_error_code
    run = db.get(Run, run_id)
    if run is None:
        return None
    message = str(error)
    run.status = RunStatus.failed
    run.error_message = message
    run.result_json = {**(run.result_json or {}), "status": "failed", "run_id": run.id, "tool_id": tool_id}
    if code := workflow_error_code(error):
        run.result_json = {**run.result_json, "error_code": code}
    db.commit()
    return run


def cancellation_result(run: Run) -> dict[str, Any]:
    metadata = run.result_json or {}
    return {"run_id": run.id, "status": run.status.value, "cancellation": metadata.get("cancellation")}


def cancel_workflow_run(db: Session, run: Run, *, cancel_job_first: bool = False) -> Run:
    """Request cancellation, and finalize runs whose worker can no longer report.

    A job still under a live worker keeps its active status until that worker
    writes the terminal row, which is what continues to hold the case. A job the
    manager no longer knows has no such callback, so it is finalized here. A
    container that outlived either path stays visible to the runtime and blocks
    the next submission by itself; it needs no stored verdict to do so.
    """
    run_id, job_id = run.id, run.job_id
    db.rollback()

    def requested() -> Run:
        current = db.get(Run, run_id)
        if current is None:
            raise ValueError(f"Workflow run {run_id!r} no longer exists")
        if not run_is_active(current):
            return current
        current.result_json = {**(current.result_json or {}), "cancellation": "requested"}
        db.commit()
        return current

    current = run_with_sqlite_lock_retry(db, requested)
    if not run_is_active(current):
        return current
    if job_id:
        job_manager.cancel(job_id)
    job = job_manager.status(job_id) if job_id else {}
    if not (job.get("stopped_before_start") or job.get("ready") or job.get("status", "unknown") == "unknown"):
        return current
    with suppress(Exception):
        # Best effort: the bridge may be gone, and its answer is no longer
        # load-bearing for whether the next submission is allowed.
        BridgeClient.from_environment().cancel(run_id)
    db.rollback()

    def stopped() -> Run:
        fresh = db.get(Run, run_id)
        if fresh is None:
            raise ValueError("Workflow run no longer exists")
        fresh.status = RunStatus.canceled
        fresh.result_json = {**(fresh.result_json or {}), "status": "canceled", "cancellation": "stopped"}
        db.commit()
        return fresh

    return run_with_sqlite_lock_retry(db, stopped)
