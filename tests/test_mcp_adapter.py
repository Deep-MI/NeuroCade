"""Local protocol, isolation, approval and documentation regression tests."""

import asyncio
import json

import httpx
import pytest
from api_service.assistant.approval_contracts import AssistantWorkflowApprovalExecution, AssistantWorkflowApprovalPresentation
from api_service.assistant.tool_execution_store import reconcile_interrupted_tool_executions
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult, ToolRisk
from api_service.documentation.tools import documentation_tools
from api_service.mcp_adapter import server, service
from api_service.mcp_adapter.auth import authenticate, token_hash, validate_local_headers
from fastapi import FastAPI, HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.routing import Route

from backend_common.case_storage import ensure_case_storage_layout, ensure_workspace_storage_layout
from backend_common.db import (
    AssistantToolExecution,
    Base,
    Case,
    McpClient,
    RoleEnum,
    User,
    Workspace,
    WorkspaceMembership,
    _configure_sqlite_connection,
    _sqlite_begin,
)
from backend_common.settings import get_settings


@pytest.fixture
def database(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "fs_data_root", tmp_path / "data")
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_access", "standard")
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "mcp.db"), connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _configure_sqlite_connection)
    event.listen(engine, "begin", _sqlite_begin)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(server, "SessionLocal", factory)
    with factory() as db:
        user = User(id="u", email="u@test.invalid", full_name="Tester")
        db.add(user)
        db.flush()
        for wid in ["w", "other"]:
            workspace = Workspace(id=wid, owner_user_id="u", name="workspace-" + wid)
            db.add(workspace)
            db.flush()
            db.add(WorkspaceMembership(workspace_id=wid, user_id="u", role=RoleEnum.owner))
            ensure_workspace_storage_layout(settings, workspace)
            case = Case(id="case-" + wid, workspace_id=wid, owner_user_id="u", title="case-" + wid)
            db.add(case)
            db.flush()
            ensure_case_storage_layout(settings, case, workspace)
        db.add(McpClient(id="c", user_id="u", workspace_id="w", name="Test", access="standard", require_approval=True, token_hash=token_hash("ncmcp_test")))
        db.commit()
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_documentation_matches_installed_catalog(database):
    with database() as db:
        _, state = service.state_for(db, "c")
        tool = {t.name: t for t in documentation_tools(state)}["docs_search"]
        result = await tool.execute(
            ToolExecutionContext(call_id="docs"), {"product": "fastsurfer", "query": "seg_only", "tool_id": "fastsurfer_full"}
        )
        assert not result.is_error
        assert result.details["version"] == "2.4.2"
        assert result.details["matched_workflow"]
        assert result.details["matches"]
        result = await tool.execute(ToolExecutionContext(call_id="docs"), {"product": "fastsurfer", "query": "flags", "version": "unknown"})
        assert result.is_error and "DOC_VERSION_UNAVAILABLE" in result.content


@pytest.mark.asyncio
async def test_scope_and_read_only(database):
    with database() as db:
        with pytest.raises(HTTPException):
            await service.invoke(db, "c", "get_case", {"case_id": "case-other", "arguments": {}})
        result = await service.invoke(db, "c", "list_cases", {"arguments": {}})
        assert "case-w" in result["result"]["content"]
        assert "case-other" not in result["result"]["content"]
        db.get(McpClient, "c").access = "read"
        db.commit()
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, "c", "tool_run_cancel", {"arguments": {"run_id": "x"}, "idempotency_key": "k"})
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_approval_retry_atomic_claim_and_revocation(database, monkeypatch):
    calls = []

    async def execute(_context, arguments):
        await asyncio.sleep(0)
        calls.append(arguments)
        return ToolResult.structured({"ok": True})

    def presentation(args):
        return AssistantWorkflowApprovalPresentation(
            kind="workflow",
            title="Test",
            description="Test",
            details="",
            execution=AssistantWorkflowApprovalExecution(mode="background", gpu=False),
        )

    tool = ToolDefinition(
        "mutation",
        "Test",
        service.schema({"value": {"type": "integer"}}, ["value"]),
        execute,
        risk=ToolRisk.workflow,
        approval_presentation=presentation,
    )
    monkeypatch.setattr(service, "definitions", lambda state: {"mutation": tool})
    payload = {"arguments": {"value": 1}, "idempotency_key": "intent"}
    with database() as db:
        planned = await service.invoke(db, "c", "mutation", payload)
        assert planned["status"] == "awaiting_approval" and not calls
        assert (await service.invoke(db, "c", "mutation", payload))["invocation_id"] == planned["invocation_id"]
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, "c", "mutation", {**payload, "arguments": {"value": 2}})
        assert exc.value.status_code == 409

    async def approve():
        with database() as db:
            return await service.decide(db, db.get(AssistantToolExecution, planned["invocation_id"]), True)

    await asyncio.gather(approve(), approve())
    assert calls == [{"value": 1}]
    with database() as db:
        replay = await service.invoke(db, "c", "mutation", payload)
        assert replay["status"] == "succeeded"
        db.get(McpClient, "c").revoked = True
        db.commit()
        with pytest.raises(HTTPException):
            authenticate(db, "Bearer ncmcp_test")


@pytest.mark.asyncio
async def test_sdk_round_trip_and_auth_gate(database):
    endpoint, manager = server.create_adapter()

    class ASGI:
        async def __call__(self, scope, receive, send):
            await endpoint(scope, receive, send)

    app = FastAPI()
    app.router.routes.append(Route("/mcp", ASGI(), methods=["POST", "GET", "DELETE"]))

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth)

    async with manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as raw:
            assert (await raw.post("/mcp", json={})).status_code == 401
            assert (await raw.post("/mcp", headers={"Origin": "https://evil.invalid"}, json={})).status_code == 403
        async with (
            streamablehttp_client("http://localhost/mcp", headers={"Authorization": "Bearer ncmcp_test"}, httpx_client_factory=factory) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert "neurocade_docs_search" in names and "neurocade_tool_call" in names
            assert "neurocade_tool_config_upsert" in names
            result = await session.call_tool("neurocade_get_context", {})
            assert result.structuredContent is not None
            assert result.structuredContent["workspace_id"] == "w"
            result = await session.call_tool("neurocade_docs_search", {"arguments": {"product": "fastsurfer", "query": "seg_only"}})
            assert not result.isError and "2.4.2" in str(result.structuredContent)
        with database() as db:
            db.get(McpClient, "c").revoked = True
            db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as raw:
            response = await raw.post("/mcp", headers={"Authorization": "Bearer ncmcp_test"}, json={})
            assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.invalid", "x-neurocade-ui": "1"},
        {"host": "localhost", "origin": "https://evil.invalid", "x-neurocade-ui": "1"},
        {"host": "localhost"},
    ],
)
def test_management_origin_guard(headers):
    with pytest.raises(HTTPException):
        validate_local_headers(headers, management=True)


@pytest.mark.asyncio
async def test_expired_approval_does_not_execute(database):
    with database() as db:
        row = AssistantToolExecution(
            source="mcp",
            client_id="c",
            user_id="u",
            workspace_id="w",
            call_id="expired",
            tool_name="tool_run_cancel",
            arguments_digest="a",
            arguments_json={"run_id": "x"},
            risk="workflow",
            status="planned",
            expires_at=1,
        )
        db.add(row)
        db.commit()
        assert (await service.decide(db, row, True))["status"] == "expired"


def test_interrupted_external_invocation_reconciles_without_chat(database):
    with database() as db:
        row = AssistantToolExecution(
            source="mcp",
            client_id="c",
            user_id="u",
            workspace_id="w",
            call_id="interrupted",
            tool_name="tool_call",
            arguments_digest="a",
            arguments_json={},
            risk="workflow",
            status="executing",
        )
        db.add(row)
        db.commit()
        reconcile_interrupted_tool_executions(db)
        db.refresh(row)
        assert row.status == "ambiguous" and row.turn_id is None and row.thread_id is None


def test_connection_file_permissions(tmp_path):
    from neurocade_mcp import load_connection

    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"url": "http://localhost:8000/mcp", "installation_id": "test-installation"}))
    path.chmod(0o644)
    with pytest.raises(ValueError):
        load_connection(path)
    path.chmod(0o600)
    assert load_connection(path)["url"].endswith("/mcp")
    path.write_text(json.dumps({"url": "http://evil.invalid/mcp"}))
    with pytest.raises(ValueError):
        load_connection(path)


@pytest.mark.asyncio
async def test_workflow_approval_queues_once_and_docs_use_run_snapshot(database, monkeypatch):
    from api_service.assistant.tools import catalog_tools
    from api_service.documentation.tools import documentation_tools
    from api_service.runtime import workflow_runs
    from api_service.runtime_tools import workflow_execution

    from backend_common.case_storage import case_storage_dir
    from backend_common.db import Run

    monkeypatch.setattr(catalog_tools, "validate_catalog_image", lambda *a, **k: None)
    monkeypatch.setattr(workflow_execution, "resolve_gpu_enabled", lambda *a, **k: False)
    submitted = []
    monkeypatch.setattr(workflow_runs, "submit_neuroimaging_workflow", lambda **kw: submitted.append(kw["run"].id) or kw["job_id"])
    with database() as db:
        path = case_storage_dir(get_settings(), "w", "case-w") / "input.nii.gz"
        path.write_bytes(b"test-input")
        payload = {
            "case_id": "case-w",
            "arguments": {"tool_id": "fastsurfer_full", "inputs": ["/case/input.nii.gz"]},
            "idempotency_key": "real-catalog",
        }
        planned = await service.invoke(db, "c", "tool_call", payload)
        assert planned["status"] == "awaiting_approval"
        assert db.query(Run).count() == 0
        done = await service.decide(db, db.get(AssistantToolExecution, planned["invocation_id"]), True)
        assert done["status"] == "succeeded", done
        assert submitted == [done["run_id"]]
        assert (await service.invoke(db, "c", "tool_call", payload))["run_id"] == done["run_id"]
        assert submitted == [done["run_id"]]
        _, state = service.state_for(db, "c", "case-w")
        docs = {tool.name: tool for tool in documentation_tools(state)}["docs_search"]
        result = await docs.execute(
            ToolExecutionContext(call_id="doc"), {"product": "fastsurfer", "query": "output", "run_id": done["run_id"]}
        )
        assert result.details["version"] == "2.4.2"
        path.write_bytes(b"changed-input")
        with pytest.raises(HTTPException) as exc:
            await service.invoke(db, "c", "tool_call", payload)
        assert "IDEMPOTENCY_CONFLICT" in str(exc.value.detail)


def test_management_pairing_cannot_use_agent_credentials(database):
    from api_service.deps import get_context, get_db
    from api_service.mcp_adapter.management import router
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    def db_override():
        with database() as db:
            yield db

    def context_override():
        with database() as db:
            return service.state_for(db, "c")[1]["context"]

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_context] = context_override
    with TestClient(app, base_url="http://localhost") as client:
        body = {"name": "Another", "workspace_id": "w", "access": "read"}
        assert client.post("/api/app/mcp/pairings", json=body).status_code == 403
        assert (
            client.post("/api/app/mcp/pairings", json=body, headers={"X-NeuroCade-UI": "1", "Origin": "https://evil.invalid"}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/app/mcp/pairings", json=body, headers={"X-NeuroCade-UI": "1", "Authorization": "Bearer ncmcp_test"}
            ).status_code
            == 403
        )
        response = client.post("/api/app/mcp/pairings", json=body, headers={"X-NeuroCade-UI": "1"})
        assert response.status_code == 200
        grant = response.json()
        redeemed = client.post("/api/app/mcp/pairings/redeem", headers={"X-NeuroCade-UI": "1"},
                               json={key: grant[key] for key in ("pairing_id", "code", "installation_id")})
        assert redeemed.status_code == 200
        created = redeemed.json()
        with database() as db:
            row = db.get(McpClient, created["client_id"])
            assert row.token_hash != created["token"]
        assert client.delete("/api/app/mcp/clients/" + created["client_id"], headers={"X-NeuroCade-UI": "1"}).status_code == 200
        assert client.post("/api/app/mcp/clients", json=body, headers={"X-NeuroCade-UI": "1"}).status_code == 405


def test_workspace_run_blocks_case_submission(database):
    from api_service.runtime.run_admission import guard_output_submission

    from backend_common.db import AssistantScope, Run, RunStatus

    @guard_output_submission
    def submit(run, workflow):
        pytest.fail("Overlapping submission should not execute")

    with database() as db:
        db.add(
            Run(
                id="workspace-run",
                workspace_id="w",
                created_by_user_id="u",
                scope_type=AssistantScope.workspace,
                status=RunStatus.queued,
                run_type="any",
            )
        )
        row = Run(id="case-run", workspace_id="w", case_id="case-w", created_by_user_id="u", status=RunStatus.queued, run_type="other")
        db.add(row)
        db.commit()
        with pytest.raises(ValueError, match="OUTPUT_BUSY"):
            submit(row, None)


def test_disabled_mcp_does_not_fall_back_to_spa():
    import os
    import subprocess
    import sys

    # The developer's running installation may enable MCP in .env. Import the
    # app in an isolated process with the disabled profile explicitly selected.
    subprocess.run([sys.executable, "-c", "from api_service.main import app; from starlette.testclient import TestClient; assert TestClient(app).get('/mcp').status_code == 404"],
        env={**os.environ, "NEUROCADE_MCP_ENABLED": "false", "PYTHONPATH": os.pathsep.join(sys.path)}, check=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("inputs", [[], ["/case/a", "/case/b"], [123], [""]])
async def test_invalid_workflow_inputs_never_create_approval(database, inputs):
    from jsonschema import ValidationError

    with database() as db:
        before = db.query(AssistantToolExecution).count()
        with pytest.raises((HTTPException, ValidationError)):
            await service.invoke(
                db,
                "c",
                "tool_call",
                {"case_id": "case-w", "arguments": {"tool_id": "fastsurfer_fast", "inputs": inputs}, "idempotency_key": "invalid-inputs"},
            )
        assert db.query(AssistantToolExecution).count() == before


@pytest.mark.asyncio
async def test_run_logs_are_scoped_bounded_and_pageable(database):
    from api_service.helpers import get_case_for_user

    from backend_common.db import Run, RunStatus
    from backend_common.run_logs import initialize_run_logs

    with database() as db:
        db.add(Case(id="sibling", workspace_id="w", owner_user_id="u", title="sibling"))
        db.add(
            Run(
                id="logs-run",
                workspace_id="w",
                case_id="case-w",
                created_by_user_id="u",
                run_type="fastsurfer_fast",
                status=RunStatus.running,
            )
        )
        db.add(
            Run(
                id="other-run",
                workspace_id="other",
                case_id="case-other",
                created_by_user_id="u",
                run_type="fastsurfer_fast",
                status=RunStatus.running,
            )
        )
        db.commit()
        ensure_case_storage_layout(get_settings(), db.get(Case, "sibling"), db.get(Workspace, "w"))
        _, _, _, root = get_case_for_user(db, "case-w", "u")
        stdout, stderr = initialize_run_logs(root, "logs-run")
        stdout.write_bytes(b"abcdefghij")
        stderr.write_bytes(b"runtime diagnostic")

        async def read(args, case="case-w"):
            result = await service.invoke(db, "c", "tool_run_logs", {"case_id": case, "arguments": args})
            return result["result"]

        first = await read({"run_id": "logs-run", "max_bytes": 4})
        assert first["details"]["text"] == "abcd"
        assert first["details"]["next_offset"] == 4 and first["details"]["has_more"]
        last = await read({"run_id": "logs-run", "offset": 8, "max_bytes": 4})
        assert last["details"]["text"] == "ij" and not last["details"]["has_more"]
        assert (await read({"run_id": "logs-run", "stream": "stderr"}))["details"]["text"] == "runtime diagnostic"
        assert (await read({"run_id": "logs-run"}, "sibling"))["is_error"]
        assert (await read({"run_id": "other-run"}))["is_error"]
        from jsonschema import ValidationError

        with pytest.raises(ValidationError):
            await read({"run_id": "logs-run", "max_bytes": 20001})


def test_run_log_reader_rejects_symlink_escape(tmp_path):
    from backend_common.run_logs import initialize_run_logs, read_run_log_page

    root = tmp_path / "authorized"
    stdout, _ = initialize_run_logs(root, "run")
    outside = tmp_path / "private"
    outside.write_text("unrelated")
    stdout.unlink()
    stdout.symlink_to(outside)
    with pytest.raises(ValueError, match="authorized root"):
        read_run_log_page(root, "run")
