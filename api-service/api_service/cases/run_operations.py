"""Case workflow submission and cancellation transactions."""

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from neurocade_runtime_tools.container_request import RuntimeBind
from neurocade_runtime_tools.runtime_backends import RuntimeGpuUnavailableError, resolve_gpu_enabled
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_service.cases.serializers import serialize_run_summary
from api_service.cases.service import (
    ensure_case_not_active,
    latest_case_run,
    raise_case_conflict,
    require_mutations_enabled,
)
from api_service.cases.uploads import _require_run_analysis_input_artifact
from api_service.helpers import get_case_for_user, log_event
from api_service.jobs import job_manager
from api_service.policies import require_case_write
from api_service.runtime import settings
from api_service.runtime.neuroimaging_tasks import submit_neuroimaging_workflow
from api_service.runtime_tools.workflow_catalog import resolve_workflow
from api_service.runtime_tools.workflow_execution import prepare_workflow
from api_service.schemas import RunSummary, StartRunRequest
from backend_common.auth import AuthContext
from backend_common.db import Run, RunStatus
from backend_common.run_logs import initialize_run_logs
from backend_common.run_statuses import TERMINAL_RUN_STATUSES
from backend_common.storage import resolve_artifact_path


def _validate_output_name_overrides(request: StartRunRequest, tool) -> dict[str, str]:
    """Validate sparse, display-only overrides for a manual workflow run."""
    declared_names = {output.name for output in tool.outputs}
    unknown_names = sorted(set(request.output_name_overrides) - declared_names)
    if unknown_names:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown workflow output name(s): {', '.join(unknown_names)}",
        )

    overrides: dict[str, str] = {}
    for output_name, value in request.output_name_overrides.items():
        display_name = value.strip()
        if not display_name:
            raise HTTPException(status_code=400, detail=f"Output name for {output_name!r} must not be empty")
        if len(display_name) > 255:
            raise HTTPException(status_code=400, detail=f"Output name for {output_name!r} is too long")
        if any(ord(character) < 32 or ord(character) == 127 for character in display_name):
            raise HTTPException(status_code=400, detail=f"Output name for {output_name!r} contains control characters")
        if display_name != output_name:
            overrides[output_name] = display_name
    return overrides


async def start_neuroimaging_run(db: Session, context: AuthContext, *, request: StartRunRequest) -> RunSummary:
    """Create and submit a catalog-defined background workflow for a case."""
    require_mutations_enabled()
    tool = resolve_workflow(request.tool_id, settings=settings, user_id=context.user.id)
    output_name_overrides = _validate_output_name_overrides(request, tool)
    try:
        gpu_enabled = resolve_gpu_enabled(tool.execution.gpu, image=tool.neurodesk_image)
    except RuntimeGpuUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(request.input_artifact_ids) != len(tool.inputs):
        raise HTTPException(
            status_code=400,
            detail=f"{tool.label} requires exactly {len(tool.inputs)} ordered input file(s)",
        )

    case, workspace, role, case_dir = get_case_for_user(db, request.case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    ensure_case_not_active(db, case)
    input_artifacts = [
        _require_run_analysis_input_artifact(db, case, artifact_id)
        for artifact_id in request.input_artifact_ids
    ]

    case_dir = case_dir.resolve()
    container_inputs = []
    for artifact in input_artifacts:
        input_path = resolve_artifact_path(artifact).resolve()
        try:
            relative = input_path.relative_to(case_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Input artifact is outside the case storage root") from exc
        container_inputs.append(f"/case/{relative.as_posix()}")

    job_id = str(uuid4())
    run = Run(
        case_id=case.id,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        status=RunStatus.queued,
        run_type=tool.id,
        input_json={
            "tool_id": tool.id,
            "input_artifact_ids": [artifact.id for artifact in input_artifacts],
            "output_name_overrides": output_name_overrides,
            "workflow_definition": tool.model_dump(mode="json", by_alias=True, exclude_none=True),
            "execution": {"device": "cuda" if gpu_enabled else "cpu"},
        },
        result_json={"status": "queued", "tool_id": tool.id},
        job_id=job_id,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise_case_conflict(exc, "Case already has an active run")
    db.refresh(run)

    try:
        prepare_workflow(
            tool.id,
            container_inputs,
            RuntimeBind(case_dir, "/case", "rw"),
            workflow=tool,
            run_id=run.id,
            gpu_enabled=gpu_enabled,
        )
        initialize_run_logs(case_dir, run.id)
        # ``db.refresh(run)`` opened a read transaction. End that snapshot
        # before durable submission writes through a separate SessionLocal;
        # otherwise the later audit insert can fail to upgrade the stale
        # SQLite snapshot with ``database is locked``.
        db.commit()
        submitted_job_id = submit_neuroimaging_workflow(
            run=run,
            workflow=tool,
            inputs=container_inputs,
            bind_host_path=case_dir,
            bind_container_path="/case",
            job_id=job_id,
            gpu_enabled=gpu_enabled,
        )
        if submitted_job_id != job_id:
            raise RuntimeError("Background worker returned an unexpected job id")
    except Exception as exc:
        run.status = RunStatus.failed
        run.error_message = str(exc)
        run.result_json = {"status": "failed", "run_id": run.id, "tool_id": tool.id}
        db.commit()
        raise

    log_event(db, context, "run.started", case_id=case.id, details={"run_id": run.id, "tool_id": tool.id})
    return serialize_run_summary(run)


def cancel_active_case_run(db: Session, context: AuthContext, *, case_id: str) -> dict:
    """Cancel the active workflow job for a case and mark its run canceled."""
    require_mutations_enabled()
    case, _workspace, role, _case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_write(role, detail="Case not found")
    latest_run = latest_case_run(db, case_id)
    if latest_run is None or latest_run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Case has no active run")
    if latest_run.job_id:
        job_manager.cancel(latest_run.job_id)
    latest_run.status = RunStatus.canceled
    latest_run.error_message = None
    latest_run.result_json = {
        "status": "canceled",
        "run_id": latest_run.id,
        "tool_id": latest_run.run_type,
    }
    db.commit()
    log_event(db, context, "run.canceled", case_id=case_id)
    return {"status": "canceled", "case_id": case_id}
