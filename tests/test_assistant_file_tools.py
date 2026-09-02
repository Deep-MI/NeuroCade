"""Test assistant file tools behavior for NeuroCade."""

import asyncio
import sys
from collections.abc import Awaitable, Coroutine
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant import runtime as assistant_runtime_module  # noqa: E402
from api_service.assistant.runtime import AssistantRuntime  # noqa: E402
from api_service.assistant.tools.definition import ToolExecutionContext, ToolResult  # noqa: E402
from api_service.runtime.gui_runtime import GuiRuntime  # noqa: E402

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import case_storage_dir, ensure_case_storage_layout  # noqa: E402
from backend_common.db import Base, Case, RoleEnum, User, Workspace, WorkspaceMembership  # noqa: E402

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    """Run assistant tool awaitables under asyncio."""
    return asyncio.run(cast(Coroutine[Any, Any, T], awaitable))


def _tool_map(runtime: AssistantRuntime, state: dict):
    """Return assistant file tools keyed by name for the given runtime state."""
    return {tool.name: tool for tool in runtime.tools.file_tools.build_tools(state)}


def _execute(tool, arguments: dict[str, Any]) -> ToolResult:
    return _run(tool.execute(ToolExecutionContext(f"test-{tool.name}"), arguments))


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _seed_workspace(db_session):
    user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
    workspace = Workspace(id="workspace-1", owner_user_id=user.id, name="workspace-1", kind="shared", is_default=False)
    other_workspace = Workspace(id="workspace-2", owner_user_id=user.id, name="workspace-2", kind="shared", is_default=False)
    case = Case(id="case-1-id", workspace_id=workspace.id, owner_user_id=user.id, title="case-1")
    other_case = Case(id="case-2-id", workspace_id=other_workspace.id, owner_user_id=user.id, title="case-2")
    db_session.add_all([user, workspace, other_workspace, case, other_case])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    ensure_case_storage_layout(assistant_runtime_module.settings, case, workspace)
    ensure_case_storage_layout(assistant_runtime_module.settings, other_case, other_workspace)
    db_session.commit()
    return AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"), workspace, other_workspace, case, other_case


def test_assistant_read_write_edit_tools_are_case_scoped(tmp_path, monkeypatch, db_session):
    data_root = tmp_path / "neurocade-data"
    monkeypatch.setattr(assistant_runtime_module.settings, "fs_data_root", data_root)
    context, workspace, _other_workspace, case, _other_case = _seed_workspace(db_session)
    runtime = AssistantRuntime(GuiRuntime())
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case.id,
    }
    tools = _tool_map(runtime, state)

    assert {"read", "write", "edit"}.issubset(tools)

    write_result = _execute(
        tools["write"],
            {
                "path": "/case/notes/report.txt",
                "content": "left hippocampus: pending\n",
            },
    )
    assert "Wrote" in write_result.content

    read_result = _execute(tools["read"], {"path": "/case/notes/report.txt"})
    assert "left hippocampus: pending" in read_result.content

    edit_result = _execute(
        tools["edit"],
            {
                "path": "/case/notes/report.txt",
                "old_text": "pending",
                "new_text": "reviewed",
            },
    )
    assert "replaced 1 occurrence" in edit_result.content
    assert (case_storage_dir(assistant_runtime_module.settings, workspace.id, case.id) / "notes" / "report.txt").read_text(encoding="utf-8") == "left hippocampus: reviewed\n"


def test_assistant_file_tools_reject_paths_outside_authorized_case(tmp_path, monkeypatch, db_session):
    data_root = tmp_path / "neurocade-data"
    monkeypatch.setattr(assistant_runtime_module.settings, "fs_data_root", data_root)
    context, workspace, other_workspace, case, other_case = _seed_workspace(db_session)
    other_file = case_storage_dir(assistant_runtime_module.settings, other_workspace.id, other_case.id) / "secret.txt"
    other_file.parent.mkdir(parents=True, exist_ok=True)
    other_file.write_text("secret", encoding="utf-8")
    runtime = AssistantRuntime(GuiRuntime())
    tools = _tool_map(
        runtime,
        {
            "db": db_session,
            "context": context,
            "scope": "case",
            "workspace_id": workspace.id,
            "case_id": case.id,
        },
    )

    try:
        _execute(tools["read"], {"path": f"/output/workspaces/{other_workspace.id}/cases/case-2/secret.txt"})
    except Exception as exc:
        assert "Only /case paths or relative paths are allowed" in str(exc)
    else:
        raise AssertionError("Read should reject paths outside the authorized case")


def test_assistant_read_supports_tail_ranges_and_literal_search(tmp_path, monkeypatch, db_session):
    data_root = tmp_path / "neurocade-data"
    monkeypatch.setattr(assistant_runtime_module.settings, "fs_data_root", data_root)
    context, workspace, _other_workspace, case, _other_case = _seed_workspace(db_session)
    case_root = case_storage_dir(assistant_runtime_module.settings, workspace.id, case.id)
    log_path = case_root / "logs" / "pipeline.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("start\nintermediate finished\nFINAL DONE\n", encoding="utf-8")
    tools = _tool_map(
        AssistantRuntime(GuiRuntime()),
        {
            "db": db_session,
            "context": context,
            "scope": "case",
            "workspace_id": workspace.id,
            "case_id": case.id,
        },
    )

    tail = _execute(tools["read"], {"path": "/case/logs/pipeline.log", "max_bytes": 11, "from_end": True})
    matches = _execute(tools["search_text"], {"path": "/case/logs/pipeline.log", "query": "finished"})

    assert "FINAL DONE" in tail.content
    assert "byte(s) before" in tail.content
    assert "2: intermediate finished" in matches.content
