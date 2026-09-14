"""Approval discovery and bounded read-history regression coverage."""

from datetime import UTC, datetime, timedelta

import pytest
from api_service.assistant.tools.definition import ToolDefinition, ToolResult
from api_service.mcp_adapter import service
from api_service.mcp_adapter.management import activity_summary, clients, invocation, invocations
from api_service.mcp_adapter.read_audit import READ_HISTORY_LIMIT, ReadAuditStore
from test_mcp_adapter import database as database

from backend_common.db import AssistantToolExecution, McpClient


def execution(identity, **values):
    return AssistantToolExecution(
        id=identity,
        source="mcp",
        client_id="c",
        user_id="u",
        workspace_id="w",
        call_id=identity,
        tool_name="list_cases",
        arguments_digest="digest",
        arguments_json={},
        risk="read",
        status="succeeded",
        **values,
    )


@pytest.mark.asyncio
async def test_pending_is_independent_from_read_history(database):
    with database() as db:
        pending = execution("pending")
        pending.status = "planned"
        pending.risk = "write"
        pending.created_at = datetime.now(UTC) - timedelta(minutes=1)
        pending.expires_at = int(datetime.now(UTC).timestamp()) + 600
        db.add(pending)
        db.add_all([execution(f"read-{i:03}", result_json={"content": "private body"}) for i in range(105)])
        db.commit()
        context = service.state_for(db, "c")[1]["context"]
        requests = invocations(after=None, limit=50, pending=True, db=db, context=context)
        assert [row["invocation_id"] for row in requests["items"]] == ["pending"]
        seen = []
        cursor = None
        for _ in range(10):
            page = invocations(after=cursor, limit=50, pending=False, db=db, context=context)
            assert all("result" not in row for row in page["items"])
            seen.extend(row["invocation_id"] for row in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == 105
        assert invocation("pending", db=db, context=context)["status"] == "awaiting_approval"


def test_connections_are_all_reachable(database):
    with database() as db:
        db.add_all(
            [
                McpClient(id=f"client-{i:03}", name="Connection", user_id="u", workspace_id="w", token_hash=f"hash-{i}", access="read")
                for i in range(105)
            ]
        )
        db.commit()
        context = service.state_for(db, "c")[1]["context"]
        seen = []
        cursor = None
        for _ in range(10):
            page = clients(after=cursor, limit=50, db=db, context=context)
            seen.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 106


@pytest.mark.asyncio
async def test_read_result_is_transient_and_oversize_is_not_persisted(database, monkeypatch):
    secret = "private artifact content"

    async def read(_context, arguments):
        return ToolResult.success(secret if not arguments.get("large") else secret * 20000)

    tool = ToolDefinition("read", "test", {"type": "object", "properties": {"large": {"type": "boolean"}}}, read)
    monkeypatch.setattr(service, "definitions", lambda state: {"read": tool})
    with database() as db:
        response = await service.invoke(db, "c", "read", {"arguments": {}})
        assert response["result"]["content"] == secret
        row = db.get(AssistantToolExecution, response["invocation_id"])
        assert secret not in str(row.result_json)
        assert row.arguments_json == {}
        assert "result" not in activity_summary(row)
        response = await service.invoke(db, "c", "read", {"arguments": {"large": True}})
        assert response["status"] == "failed"
        assert response["result"]["details"]["code"] == "RESULT_TOO_LARGE"
        row = db.get(AssistantToolExecution, response["invocation_id"])
        assert len(str(row.result_json)) < 1000
        assert secret not in str(row.result_json)


def test_read_retention_preserves_mutations(database):
    with database() as db:
        now = datetime.now(UTC)
        old = execution("old", created_at=now - timedelta(days=8))
        mutation = execution("mutation", created_at=now - timedelta(days=8))
        mutation.risk = "workflow"
        db.add_all([old, mutation])
        db.add_all([execution(f"read-{i:04}", created_at=now) for i in range(READ_HISTORY_LIMIT + 2)])
        current = execution("current", created_at=now)
        db.add(current)
        db.commit()
        ReadAuditStore.complete(db, current, ToolResult.success("result"))
        assert db.query(AssistantToolExecution).filter_by(risk="read").count() == READ_HISTORY_LIMIT
        assert db.query(AssistantToolExecution).filter_by(id="old").count() == 0
        assert db.query(AssistantToolExecution).filter_by(id="mutation").count() == 1
