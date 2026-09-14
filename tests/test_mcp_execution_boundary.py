"""Approval snapshots must remain valid through slow work and final admission."""

import asyncio
import threading

import pytest
from api_service.assistant.invocation import ToolInvocationService
from api_service.assistant.tools import catalog_tools
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from api_service.mcp_adapter import service
from api_service.runtime import workflow_runs
from api_service.runtime.run_admission import serialize_submission
from api_service.runtime_tools import workflow_execution
from test_mcp_adapter import database as database

from backend_common.case_storage import case_storage_dir
from backend_common.db import AssistantToolExecution, Run
from backend_common.settings import get_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["preset", "input", "revoke", "read_access", "membership"])
async def test_approval_rechecks_after_image_validation(database, monkeypatch, change):
    entered, release = threading.Event(), threading.Event()

    def validate_image(*args, **kwargs):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(catalog_tools, "validate_catalog_image", validate_image)
    monkeypatch.setattr(workflow_execution, "resolve_gpu_enabled", lambda *a, **k: False)
    submitted = []
    monkeypatch.setattr(workflow_runs, "submit_neuroimaging_workflow", lambda **kw: submitted.append(kw))
    original = service.resolve_workflow("fastsurfer_full", settings=get_settings(), user_id="u")
    current = [original]
    monkeypatch.setattr(service, "resolve_workflow", lambda *a, **k: current[0])
    from api_service.assistant.tools import workflow_inputs
    monkeypatch.setattr(workflow_inputs, "resolve_workflow", lambda *a, **k: current[0])
    with database() as db:
        path = case_storage_dir(get_settings(), "w", "case-w") / "input.nii.gz"
        path.write_bytes(b"input")
        planned = await service.invoke(db, "c", "tool_call", {
            "case_id": "case-w", "idempotency_key": "race",
            "arguments": {"tool_id": "fastsurfer_full", "inputs": ["/case/input.nii.gz"]},
        })
        row = db.get(AssistantToolExecution, planned["invocation_id"])
        task = asyncio.create_task(service.decide(db, row, True))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if change == "preset":
                current[0] = original.model_copy(update={"script": original.script + "\n# changed during validation"})
            elif change == "input":
                path.write_bytes(b"changed after approval")
            else:
                from backend_common.db import McpClient, WorkspaceMembership

                with database() as other:
                    if change == "revoke":
                        other.get(McpClient, "c").revoked = True
                    elif change == "read_access":
                        other.get(McpClient, "c").access = "read"
                    else:
                        other.query(WorkspaceMembership).filter_by(workspace_id="w", user_id="u").delete()
                    other.commit()
        finally:
            release.set()
        result = await task
        assert result["status"] == "failed", result
        assert db.query(Run).count() == 0
        assert not submitted


@pytest.mark.asyncio
async def test_execute_claimed_rejects_unclaimed_invocation(database):
    calls = []

    async def execute(_context, _arguments):
        calls.append(True)
        return ToolResult.structured({"ok": True})

    tool = ToolDefinition("read", "read", service.schema(), execute)
    with database() as db:
        row = AssistantToolExecution(source="mcp", client_id="c", user_id="u", workspace_id="w",
                                     call_id="claim", tool_name="read", arguments_digest="unused",
                                     arguments_json={}, risk="read", status="planned")
        db.add(row)
        db.commit()
        with pytest.raises(ValueError, match="not claimed"):
            await ToolInvocationService.execute_claimed(
                tool, ToolExecutionContext(call_id="claim", execution_id=row.id), {},
                store=service.store, db=db, execution=row,
            )
        assert not calls
        assert row.status == "planned"


def test_admission_rejects_async_critical_section():
    async def unsafe():
        await asyncio.sleep(0)

    with pytest.raises(TypeError, match="synchronous"):
        serialize_submission(unsafe)


@pytest.mark.asyncio
async def test_admission_retries_authorization_after_conflicting_commit(database, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend_common.db import McpClient

    monkeypatch.setattr(catalog_tools, "validate_catalog_image", lambda *a, **k: None)
    monkeypatch.setattr(workflow_execution, "resolve_gpu_enabled", lambda *a, **k: False)
    submitted = []
    monkeypatch.setattr(workflow_runs, "submit_neuroimaging_workflow", lambda **kw: submitted.append(kw))
    with database() as db:
        path = case_storage_dir(get_settings(), "w", "case-w") / "input.nii.gz"
        path.write_bytes(b"input")
        planned = await service.invoke(db, "c", "tool_call", {
            "case_id": "case-w", "idempotency_key": "retry-revalidation",
            "arguments": {"tool_id": "fastsurfer_full", "inputs": ["/case/input.nii.gz"]},
        })
        commit = db.commit
        conflicted = False

        def conflict_once():
            nonlocal conflicted
            if not conflicted and any(isinstance(row, Run) for row in db.new):
                conflicted = True
                db.rollback()
                with database() as other:
                    other.get(McpClient, "c").revoked = True
                    other.commit()
                raise OperationalError("INSERT runs", {}, Exception("database is locked"))
            return commit()

        monkeypatch.setattr(db, "commit", conflict_once)
        result = await service.decide(db, db.get(AssistantToolExecution, planned["invocation_id"]), True)
        assert conflicted
        assert result["status"] == "failed"
        assert db.query(Run).count() == 0
        assert not submitted


@pytest.mark.asyncio
async def test_completion_survives_real_worker_commit_after_submission(database, monkeypatch):
    from api_service.jobs.manager import JobManager

    from backend_common.db import RunStatus

    monkeypatch.setattr(catalog_tools, "validate_catalog_image", lambda *a, **k: None)
    monkeypatch.setattr(workflow_execution, "resolve_gpu_enabled", lambda *a, **k: False)
    worker = JobManager(concurrency={"fastsurfer": 1})
    committed = threading.Event()
    submissions = []

    def update_run(run_id):
        with database() as other:
            run = other.get(Run, run_id)
            run.status = RunStatus.running
            other.commit()
        committed.set()

    worker.register("test.commit", update_run)
    try:
        with database() as db:
            path = case_storage_dir(get_settings(), "w", "case-w") / "input.nii.gz"
            path.write_bytes(b"input")
            planned = await service.invoke(db, "c", "tool_call", {
                "case_id": "case-w", "idempotency_key": "worker-race",
                "arguments": {"tool_id": "fastsurfer_full", "inputs": ["/case/input.nii.gz"]},
            })
            row = db.get(AssistantToolExecution, planned["invocation_id"])

            def submit(**kw):
                submissions.append(kw["run"].id)
                # Model the production stale read snapshot while a real worker commits.
                db.refresh(row)
                worker.submit("test.commit", {"run_id": kw["run"].id}, queue="fastsurfer", job_id=kw["job_id"])
                assert committed.wait(5)
                return kw["job_id"]

            monkeypatch.setattr(workflow_runs, "submit_neuroimaging_workflow", submit)
            result = await service.decide(db, row, True)
            assert result["status"] == "succeeded"
            assert len(submissions) == 1
            assert db.get(Run, result["run_id"]).status == RunStatus.running
            assert db.get(AssistantToolExecution, result["invocation_id"]).status == "succeeded"
    finally:
        worker.shutdown(wait=True)


@pytest.mark.asyncio
async def test_completion_retry_never_reexecutes_handler(database, monkeypatch):
    from api_service.assistant.tool_execution_store import AssistantToolExecutionStore
    from sqlalchemy.exc import OperationalError

    calls = []
    async def handler(_context, _arguments):
        calls.append(True)
        return ToolResult.success("accepted")

    tool = ToolDefinition("read", "read", service.schema(), handler)
    with database() as db:
        row = AssistantToolExecution(source="mcp", client_id="c", user_id="u", workspace_id="w",
                                     call_id="completion-retry", tool_name="read", arguments_digest="unused",
                                     arguments_json={}, risk="read", status="executing")
        db.add(row)
        db.commit()
        context = ToolExecutionContext(call_id=row.call_id, execution_id=row.id)
        original_commit = db.commit
        attempts = 0
        def commit():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("UPDATE assistant_tool_executions", {}, Exception("database is locked"))
            return original_commit()
        monkeypatch.setattr(db, "commit", commit)
        result = await ToolInvocationService.execute_claimed(tool, context, {}, store=AssistantToolExecutionStore(), db=db, execution=row)
        assert not result.is_error
        assert attempts == 2 and calls == [True]
        assert row.status == "succeeded"
        # A second completion cannot overwrite the first durable outcome.
        AssistantToolExecutionStore.complete(db, row, ToolResult.error("late duplicate"))
        assert row.status == "succeeded" and row.result_json["content"] == "accepted"
