"""Case navigation shares scope enforcement across local and MCP agents."""

import pytest
from api_service import browser_navigation as navigation
from api_service.assistant.tools.definition import ToolExecutionContext
from api_service.assistant.tools.navigation_tools import navigation_tools
from api_service.mcp_adapter import service
from fastapi import HTTPException
from test_mcp_adapter import database as database


@pytest.fixture(autouse=True)
def clean_sessions():
    navigation._sessions.clear()
    yield
    navigation._sessions.clear()


@pytest.mark.asyncio
async def test_local_workspace_open_case_and_ack(database):
    navigation.sync("u", "tab", "w", None)
    with database() as db:
        _, state = service.state_for(db, "c")
        state["gui_session_id"] = "tab"
        tool = navigation_tools(state)[0]
        result = await tool.execute(ToolExecutionContext(call_id="open"), {"case_id": "case-w"})
        assert result.details["status"] == "queued"
    command = navigation.sync("u", "tab", "w", None)["command"]
    assert command is not None
    assert command["case_id"] == "case-w"
    assert navigation.sync("u", "tab", "w", command["id"])["command"] is None


@pytest.mark.asyncio
async def test_mcp_open_case_scope_and_readonly(database):
    from backend_common.db import McpClient

    navigation.sync("u", "tab", "w", None)
    with database() as db:
        result = await service.invoke(db, "c", "open_case", {"arguments": {"case_id": "case-w"}})
        assert result["result"]["details"]["status"] == "queued"
        denied = await service.invoke(db, "c", "open_case", {"arguments": {"case_id": "case-other"}})
        assert denied["status"] == "failed"
        command = navigation.sync("u", "tab", "w", None)["command"]
        assert command is not None and command["case_id"] == "case-w"
        db.get(McpClient, "c").access = "read"
        db.commit()
        with pytest.raises(HTTPException):
            await service.invoke(db, "c", "open_case", {"arguments": {"case_id": "case-w"}})


def test_browser_selection_is_scoped_expiring_and_not_broadcast(monkeypatch):
    navigation.sync("other-user", "other", "w", None)
    navigation.sync("u", "wrong-workspace", "other", None)
    assert navigation.open_case("u", "w", "c")["status"] == "browser_required"
    navigation.sync("u", "one", "w", None)
    navigation.sync("u", "two", None, None)
    assert navigation.open_case("u", "w", "c")["status"] == "browser_required"
    result = navigation.open_case("u", "w", "c", "one")
    assert result["status"] == "queued"
    assert navigation.sync("u", "two", None, None)["command"] is None
    now = navigation.time.monotonic()
    monkeypatch.setattr(navigation.time, "monotonic", lambda: now + 31)
    assert navigation.open_case("u", "w", "c", "one")["status"] == "browser_required"
