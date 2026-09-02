"""Background execution for catalog-defined neuroimaging workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api_service.jobs import job_manager
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow, resolve_workflow
from api_service.runtime_tools.workflow_execution import execute_workflow
from backend_common.artifact_reconciliation import reconcile_artifacts
from backend_common.db import Artifact, Run, RunStatus, SessionLocal, run_with_sqlite_lock_retry
from backend_common.run_logs import initialize_run_logs

RUN_NEUROIMAGING_WORKFLOW_TASK = "api_service.neuroimaging.run_workflow"


def _update_run(run_id: str, *, status: RunStatus, result: dict[str, Any], error: str | None = None) -> bool:
    with SessionLocal() as db:
        def operation() -> bool:
            run = db.get(Run, run_id)
            if run is None or run.status == RunStatus.canceled:
                return False
            run.status = status
            run.result_json = result
            run.error_message = error
            db.commit()
            return True

        return run_with_sqlite_lock_retry(db, operation)


def _store_canceled_result(run_id: str, result: dict[str, Any]) -> None:
    """Retain output provenance without changing a run's canceled status."""
    with SessionLocal() as db:
        def operation() -> None:
            run = db.get(Run, run_id)
            if run is None or run.status != RunStatus.canceled:
                return
            run.result_json = result
            db.commit()

        run_with_sqlite_lock_retry(db, operation)


def _reconcile_case_artifacts(case_id: str | None) -> None:
    if case_id is None:
        return
    with SessionLocal() as db:
        def operation() -> None:
            artifacts = db.query(Artifact).filter(Artifact.case_id == case_id).all()
            reconcile_artifacts(db, artifacts)
            db.commit()

        run_with_sqlite_lock_retry(db, operation)


def run_neuroimaging_workflow_task(
    *,
    run_id: str,
    tool_id: str,
    inputs: list[str],
    bind_host_path: str,
    bind_container_path: str,
    case_id: str | None,
    gpu_enabled: bool,
    workflow_definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one background workflow and persist its durable run lifecycle."""
    from neurocade_runtime_tools.container_request import RuntimeBind

    host_root = Path(bind_host_path).resolve()
    stdout_path, stderr_path = initialize_run_logs(host_root, run_id)

    running = {"status": "running", "run_id": run_id, "tool_id": tool_id}
    try:
        started = _update_run(run_id, status=RunStatus.running, result=running)
    except Exception as exc:
        failed = {
            "status": "failed",
            "run_id": run_id,
            "tool_id": tool_id,
            "return_code": None,
            "stderr": f"Failed to start workflow: {exc}",
        }
        _update_run(run_id, status=RunStatus.failed, result=failed, error=failed["stderr"])
        return failed
    if not started:
        return {"status": "canceled", "run_id": run_id, "tool_id": tool_id}

    try:
        workflow = NeuroimagingWorkflow.model_validate(workflow_definition) if workflow_definition is not None else None
        with SessionLocal() as db:
            result = execute_workflow(
                tool_id,
                inputs,
                RuntimeBind(host_root, bind_container_path, "rw"),
                workflow=workflow,
                run_id=run_id,
                gpu_enabled=gpu_enabled,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                artifact_case_id=case_id,
                db=db,
            )
            db.commit()
    except Exception as exc:
        result = {
            "status": "failed",
            "run_id": run_id,
            "tool_id": tool_id,
            "return_code": None,
            "stderr": str(exc),
        }

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        canceled = run is not None and run.status == RunStatus.canceled
    if canceled:
        canceled_result: dict[str, Any] = {"status": "canceled", "run_id": run_id, "tool_id": tool_id}
        outputs = result.get("outputs")
        if isinstance(outputs, list):
            canceled_result["outputs"] = outputs
        _store_canceled_result(run_id, canceled_result)
        _reconcile_case_artifacts(case_id)
        return canceled_result

    succeeded = result.get("status") == "completed"
    error = None if succeeded else str(result.get("stderr") or "Workflow execution failed")
    _update_run(
        run_id,
        status=RunStatus.completed if succeeded else RunStatus.failed,
        result=result,
        error=error,
    )
    _reconcile_case_artifacts(case_id)
    return result


def submit_neuroimaging_workflow(
    *,
    run: Run,
    tool_id: str | None = None,
    workflow: NeuroimagingWorkflow | None = None,
    inputs: list[str],
    bind_host_path: Path,
    bind_container_path: str,
    job_id: str,
    gpu_enabled: bool,
) -> str:
    """Submit a pre-persisted Run to the durable in-process worker."""
    resolved_workflow = workflow or resolve_workflow(str(tool_id or ""))
    submitted_job_id = job_manager.submit(
        RUN_NEUROIMAGING_WORKFLOW_TASK,
        {
            "run_id": run.id,
            "tool_id": resolved_workflow.id,
            "inputs": inputs,
            "bind_host_path": str(bind_host_path),
            "bind_container_path": bind_container_path,
            "case_id": run.case_id,
            "gpu_enabled": gpu_enabled,
            "workflow_definition": resolved_workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        },
        queue=resolved_workflow.execution.queue,
        job_id=job_id,
    )
    return submitted_job_id


def register_neuroimaging_tasks() -> None:
    """Register the generic catalog workflow task."""
    job_manager.register(RUN_NEUROIMAGING_WORKFLOW_TASK, run_neuroimaging_workflow_task)
