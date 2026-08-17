"""Catalog-defined neuroimaging workflow execution for the assistant."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from neurocade_runtime_tools.apptainer_runtime import require_network_disabled_image
from neurocade_runtime_tools.container_request import RuntimeBind
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from api_service.assistant.tools.definition import ToolResult
from api_service.helpers import get_case_for_user, get_workspace_for_user
from api_service.policies import require_case_write, require_workspace_write
from api_service.runtime import workflow_runs
from api_service.runtime_tools.case_resolver import CONTAINER_CASE_ROOT
from api_service.runtime_tools.workflow_catalog import resolve_workflow
from api_service.runtime_tools.workflow_execution import prepare_workflow
from backend_common.case_storage import workspace_storage_dir
from backend_common.db import AssistantScope, Run, RunStatus
from backend_common.run_statuses import TERMINAL_RUN_STATUSES

RUN_STATUS_POLL_INTERVAL_SECONDS = 0.1


class CatalogToolCallArgs(BaseModel):
    """The only model-controlled workflow invocation fields."""

    tool_id: str = Field(..., description="Exact catalog workflow id returned by tool_search.")
    inputs: list[str] = Field(default_factory=list, description="Ordered absolute /case or /workspace input file paths.")

    model_config = {"extra": "forbid"}


class CatalogRunArgs(BaseModel):
    run_id: str = Field(..., description="Background workflow run id returned by tool_call.")


class CatalogRunListArgs(BaseModel):
    limit: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum number of recent workflow runs to return. Defaults to 10.",
    )

    model_config = {"extra": "forbid"}


class AssistantCatalogExecutor:
    """Resolve and execute fixed catalog workflows for assistant requests."""

    def __init__(self, *, settings) -> None:
        self.settings = settings

    @staticmethod
    def _queued_payload(
        tool,
        run: Run,
        *,
        status: str | None = None,
        idempotent_replay: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool_id": tool.id,
            "run_id": run.id,
            "status": status or run.status.value,
            "execution": workflow_runs.workflow_execution_details(
                tool,
                gpu_enabled=tool.execution.gpu,
            ),
        }
        if idempotent_replay:
            payload["idempotent_replay"] = True
        return payload

    def catalog_runtime_binds(self, state: dict[str, Any]) -> list[RuntimeBind]:
        """Return the single authorized writable root for the assistant scope."""
        db = state.get("db")
        context = state.get("context")
        workspace_id = state.get("workspace_id")
        if db is None or context is None or workspace_id is None:
            return []
        if state.get("scope") != AssistantScope.case.value:
            workspace, role = get_workspace_for_user(db, workspace_id, context.user.id)
            require_workspace_write(role)
            workspace_root = workspace_storage_dir(self.settings, workspace.id)
            workspace_root.mkdir(parents=True, exist_ok=True)
            return [RuntimeBind(workspace_root, "/workspace", "rw")]

        case_id = state.get("case_id")
        if case_id is None:
            return []
        _case, _workspace, role, case_dir = get_case_for_user(
            db, case_id, context.user.id, workspace_id=workspace_id
        )
        require_case_write(role)
        return [RuntimeBind(case_dir, CONTAINER_CASE_ROOT, "rw")]

    def catalog_tool_call(
        self,
        arguments: dict[str, Any],
        binds: list[RuntimeBind] | None = None,
        *,
        db=None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        case_id: str | None = None,
        scope: str = AssistantScope.case.value,
        run_id: str | None = None,
    ) -> ToolResult:
        """Durably enqueue a catalog workflow for cancellable execution."""
        try:
            parsed = CatalogToolCallArgs.model_validate(arguments)
            tool = resolve_workflow(
                parsed.tool_id,
                settings=self.settings,
                user_id=user_id,
            )
            require_network_disabled_image(tool.neurodesk_image)
            if not binds or len(binds) != 1:
                raise ValueError("A catalog workflow requires one active case or workspace.")
            prepared = prepare_workflow(
                tool.id,
                parsed.inputs,
                binds[0],
                workflow=tool,
                run_id=run_id,
            )
        except Exception as exc:
            return ToolResult.error(f"Error preparing tool execution: {exc}")

        if db is None or user_id is None or workspace_id is None:
            return ToolResult.error("Error preparing tool execution: workflows require an authenticated workspace.")
        existing = db.get(Run, prepared.run_id)
        if existing is not None:
            expected_inputs = list((existing.input_json or {}).get("inputs") or [])
            if (
                existing.workspace_id != workspace_id
                or existing.case_id != case_id
                or existing.created_by_user_id != user_id
                or existing.run_type != tool.id
                or expected_inputs != parsed.inputs
            ):
                return ToolResult.error("Error submitting background workflow: idempotency key conflicts with another run.")
            return ToolResult.structured(self._queued_payload(tool, existing, idempotent_replay=True))
        job_id = str(uuid4())
        run = Run(
            id=prepared.run_id,
            case_id=case_id,
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            scope_type=AssistantScope(scope),
            status=RunStatus.queued,
            run_type=tool.id,
            input_json={
                "tool_id": tool.id,
                "inputs": parsed.inputs,
                **workflow_runs.workflow_run_snapshot(tool, gpu_enabled=prepared.gpu_enabled),
            },
            result_json={"status": "queued", "tool_id": tool.id, "run_id": prepared.run_id},
            job_id=job_id,
        )
        db.add(run)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if db.get(Run, prepared.run_id) is not None:
                return self.catalog_tool_call(
                    arguments,
                    binds,
                    db=db,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    case_id=case_id,
                    scope=scope,
                    run_id=prepared.run_id,
                )
            return ToolResult.error(f"Error submitting background workflow: {exc}")
        try:
            workflow_runs.submit_workflow_run(
                run,
                tool,
                parsed.inputs,
                bind_host_path=prepared.host_root,
                bind_container_path=prepared.container_root,
                job_id=job_id,
                gpu_enabled=prepared.gpu_enabled,
            )
        except Exception as exc:
            db.rollback()
            workflow_runs.mark_workflow_run_failed(db, prepared.run_id, tool.id, exc)
            return ToolResult.error(f"Error submitting background workflow: {exc}")
        return ToolResult.structured(self._queued_payload(tool, run, status="queued"))

    @staticmethod
    def run_status(db, *, run_id: str, workspace_id: str, case_id: str | None = None) -> ToolResult:
        """Return a durable background workflow result in the active workspace."""
        run = db.get(Run, run_id)
        if run is None or run.workspace_id != workspace_id or (case_id is not None and run.case_id != case_id):
            return ToolResult.error(f"Error: workflow run {run_id!r} was not found.")
        payload = {
            "run_id": run.id,
            "tool_id": run.run_type,
            "status": run.status.value,
            "result": run.result_json,
            "error": run.error_message,
        }
        return ToolResult.structured(
            payload,
            is_error=run.status in {RunStatus.failed, RunStatus.canceled},
        )

    @staticmethod
    def list_runs(db, *, workspace_id: str, limit: int = 10, case_id: str | None = None) -> ToolResult:
        """Return recent durable workflow runs in the active assistant scope."""
        query = db.query(Run).filter(Run.workspace_id == workspace_id)
        if case_id is not None:
            query = query.filter(Run.case_id == case_id)
        runs = query.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit).all()
        payload = [
            {
                "run_id": run.id,
                "tool_id": run.run_type,
                "status": run.status.value,
                "case_id": run.case_id,
                "created_by_user_id": run.created_by_user_id,
                "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat(),
            }
            for run in runs
        ]
        return ToolResult.structured(payload, details={"runs": payload, "limit": limit})

    async def wait_for_terminal_run(
        self,
        db,
        *,
        run_id: str,
        workspace_id: str,
        case_id: str | None = None,
    ) -> ToolResult | None:
        """Wait for a run to finish, returning ``None`` when the wait expires."""
        deadline = time.monotonic() + self.settings.assistant_workflow_wait_seconds
        while True:
            db.rollback()
            run = db.get(Run, run_id)
            if run is None or run.workspace_id != workspace_id or (case_id is not None and run.case_id != case_id):
                result = self.run_status(db, run_id=run_id, workspace_id=workspace_id, case_id=case_id)
                db.rollback()
                return result
            if run.status in TERMINAL_RUN_STATUSES:
                result = self.run_status(db, run_id=run_id, workspace_id=workspace_id, case_id=case_id)
                db.rollback()
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                db.rollback()
                return None
            await asyncio.sleep(min(RUN_STATUS_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def cancel_run(db, *, run_id: str, workspace_id: str, case_id: str | None = None) -> ToolResult:
        """Cancel a queued/running workflow in the active workspace."""
        run = db.get(Run, run_id)
        if run is None or run.workspace_id != workspace_id or (case_id is not None and run.case_id != case_id):
            return ToolResult.error(f"Error: workflow run {run_id!r} was not found.")
        if run.status in TERMINAL_RUN_STATUSES:
            payload = {"run_id": run.id, "status": run.status.value}
            return ToolResult.structured(payload)
        workflow_runs.cancel_workflow_run(db, run)
        payload = {"run_id": run_id, "status": "canceled"}
        return ToolResult.structured(payload)
