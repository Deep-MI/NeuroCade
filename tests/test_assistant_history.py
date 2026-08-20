"""Test assistant history behavior for NeuroCade."""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant.conversation_store import (  # noqa: E402
    AssistantHistoryStore,
    get_or_create_thread,
    load_thread_history,
    persist_turn,
)

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import (  # noqa: E402
    AssistantMessage,
    AssistantThread,
    AssistantTurn,
    Base,
    Case,
    RoleEnum,
    User,
    Workspace,
    WorkspaceMembership,
)


def test_persist_turn_stores_display_history(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(
            id="workspace-1",
            owner_user_id=user.id,
            name="personal-workspace",
            kind="personal",
            is_default=True,
        )
        case = Case(id="case-1-id", workspace_id=workspace.id, owner_user_id=user.id, title="case-1")
        db.add_all([user, workspace, case])
        db.commit()
        context = AuthContext(user=user, role=RoleEnum.owner, auth_mode="local")
        thread = get_or_create_thread(
            db,
            context,
            scope="case",
            workspace_id=workspace.id,
            case_id=case.id,
            provider_name="provider",
            model_name="model",
        )

        persist_turn(
            db,
            context,
            thread,
            incoming_messages=[{"role": "user", "content": "Please inspect the case"}],
            tool_calls_log=[{"name": "case_file_tree", "arguments": {}, "result": "/case/mri/orig.mgz"}],
            reasoning_entries=[{"round": 1, "summary": "Inspect files", "tool_names": ["case_file_tree"]}],
            assistant_content="I found orig.mgz.",
        )

        assert db.query(AssistantMessage).count() == 3
        history = AssistantHistoryStore().list_history(
            db, user_id=user.id, scope="case", workspace_id=workspace.id, case_id=case.id
        )
        assert [message.role for message in history] == ["user", "tool-calls", "assistant"]
        assert history[-1].content == "I found orig.mgz."
        model_history = load_thread_history(db, thread.id)
        assert model_history[1] == {
            "role": "tool",
            "content": "case_file_tree({}): /case/mri/orig.mgz",
        }

        persist_turn(
            db,
            context,
            thread,
            incoming_messages=[{"role": "user", "content": "Inspect more"}],
            tool_calls_log=[],
            reasoning_entries=[],
            assistant_content="HEAD" + ("x" * 2000) + "TAIL",
        )
        monkeypatch.setattr(
            "api_service.assistant.conversation_store.settings.assistant_history_max_characters",
            500,
        )

        bounded_history = load_thread_history(db, thread.id)

        assert bounded_history[0]["role"] == "context"
        assert "History context notice" in bounded_history[0]["content"]
        assert "HEAD" in bounded_history[-1]["content"]
        assert "TAIL" in bounded_history[-1]["content"]
        assert "omitted" in bounded_history[-1]["content"]
        assert sum(len(json.dumps(message)) for message in bounded_history) <= 500
    finally:
        db.close()


def test_history_state_returns_thread_messages_and_pending_approval():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(
            id="workspace-1",
            owner_user_id=user.id,
            name="personal-workspace",
            kind="personal",
            is_default=True,
        )
        db.add_all([user, workspace])
        db.commit()
        context = AuthContext(user=user, role=RoleEnum.owner, auth_mode="local")
        thread = get_or_create_thread(
            db,
            context,
            scope="workspace",
            workspace_id=workspace.id,
            case_id=None,
            provider_name="provider",
            model_name="model",
        )
        persist_turn(
            db,
            context,
            thread,
            incoming_messages=[{"role": "user", "content": "Write the report"}],
            tool_calls_log=[],
            reasoning_entries=[],
            assistant_content="Please approve the file write.",
        )
        approval = {
            "name": "write",
            "call_id": "call-1",
            "execution_id": "execution-1",
            "arguments": {"path": "report.txt", "content": "ready"},
            "digest": "a" * 64,
            "description": "write `report.txt`",
        }
        db.add(
            AssistantTurn(
                id="turn-1",
                thread_id=thread.id,
                workspace_id=workspace.id,
                user_id=user.id,
                status="awaiting_approval",
                request_json={},
                result_json={"approval_request": approval},
            )
        )
        db.commit()

        state = AssistantHistoryStore().history_state(
            db,
            user_id=user.id,
            scope="workspace",
            workspace_id=workspace.id,
        )

        assert state.thread_key == thread.thread_key
        assert [message.content for message in state.messages] == [
            "Write the report",
            "Please approve the file write.",
        ]
        assert state.pending_approval is not None
        assert state.pending_approval.model_dump() == {**approval, "presentation": None}
    finally:
        db.close()


def test_assistant_threads_and_histories_are_private_per_user():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        first = User(id="user-1", external_auth_id="user-1", email="one@example.com", full_name="One")
        second = User(id="user-2", external_auth_id="user-2", email="two@example.com", full_name="Two")
        workspace = Workspace(id="workspace-1", owner_user_id=first.id, name="workspace", kind="shared", is_default=False)
        db.add_all([first, second, workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(workspace_id=workspace.id, user_id=first.id, role=RoleEnum.owner),
                WorkspaceMembership(workspace_id=workspace.id, user_id=second.id, role=RoleEnum.user),
            ]
        )
        db.commit()
        first_context = AuthContext(user=first, role=RoleEnum.owner, auth_mode="local")
        second_context = AuthContext(user=second, role=RoleEnum.user, auth_mode="local")
        first_thread = get_or_create_thread(
            db,
            first_context,
            scope="workspace",
            workspace_id=workspace.id,
            case_id=None,
            provider_name="provider",
            model_name="model",
        )
        second_thread = get_or_create_thread(
            db,
            second_context,
            scope="workspace",
            workspace_id=workspace.id,
            case_id=None,
            provider_name="provider",
            model_name="model",
        )
        persist_turn(
            db,
            first_context,
            first_thread,
            incoming_messages=[{"role": "user", "content": "private question"}],
            tool_calls_log=[],
            reasoning_entries=[],
            assistant_content="private answer",
        )

        store = AssistantHistoryStore()
        assert first_thread.id != second_thread.id
        assert [message.content for message in store.list_history(
            db, user_id=first.id, scope="workspace", workspace_id=workspace.id
        )] == ["private question", "private answer"]
        assert store.list_history(db, user_id=second.id, scope="workspace", workspace_id=workspace.id) == []
    finally:
        db.close()


def test_concurrent_turns_preserve_complete_unique_history(tmp_path):
    """Concurrent requests to one thread must not lose or overlap messages."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'assistant-stress.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with session_factory() as db:
        user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(
            id="workspace-1",
            owner_user_id=user.id,
            name="workspace",
            kind="personal",
            is_default=True,
        )
        db.add_all([user, workspace])
        db.commit()
        context = AuthContext(user=user, role=RoleEnum.owner, auth_mode="local")
        thread = get_or_create_thread(
            db,
            context,
            scope="workspace",
            workspace_id=workspace.id,
            case_id=None,
            provider_name="provider",
            model_name="model",
        )
        db.commit()
        thread_id = thread.id

    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def persist(index: int) -> None:
        with session_factory() as db:
            user = db.get(User, "user-1")
            thread = db.get(AssistantThread, thread_id)
            assert user is not None and thread is not None
            barrier.wait()
            persist_turn(
                db,
                AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"),
                thread,
                incoming_messages=[{"role": "user", "content": f"question-{index}"}],
                tool_calls_log=[],
                reasoning_entries=[],
                assistant_content=f"answer-{index}",
            )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(persist, range(worker_count)))

    with session_factory() as db:
        rows = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.thread_id == thread_id)
            .order_by(AssistantMessage.sequence)
            .all()
        )
        assert len(rows) == worker_count * 2
        assert [row.sequence for row in rows] == list(range(1, worker_count * 2 + 1))
        contents = {row.content_json["value"] for row in rows}
        assert contents == {
            *(f"question-{index}" for index in range(worker_count)),
            *(f"answer-{index}" for index in range(worker_count)),
        }
