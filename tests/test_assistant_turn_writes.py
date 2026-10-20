"""Real WAL regressions for assistant checkpoint and final-status persistence."""
import sqlite3

import pytest
from api_service.assistant.turn_store import AssistantTurnStore
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend_common.db import (
    AssistantThread,
    AssistantTurn,
    Base,
    User,
    Workspace,
    _configure_sqlite_connection,
    _sqlite_begin,
)


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'turns.db'}")
    event.listen(engine, "connect", _configure_sqlite_connection)
    event.listen(engine, "begin", _sqlite_begin)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id="u", email="test@example.invalid", full_name="Test"))
        db.flush()
        db.add(Workspace(id="w", owner_user_id="u", name="Test"))
        db.flush()
        db.add(AssistantThread(id="thread", thread_key="test", workspace_id="w", provider_name="test", model_name="test"))
        db.flush()
        db.add(AssistantTurn(id="turn", thread_id="thread", workspace_id="w", user_id="u", result_json={"checkpoint": {"old": True}, "keep": True}))
        db.commit()
    yield factory, engine
    engine.dispose()


@pytest.mark.parametrize("operation", ["checkpoint", "finish"])
@pytest.mark.parametrize("busy_once", [False, True])
def test_turn_write_restarts_stale_snapshot_and_retries_only_persistence(sessions, operation, busy_once):
    factory, engine = sessions
    attempts = 0

    def contend(conn, cursor, statement, parameters, context, executemany):
        nonlocal attempts
        if statement.startswith("UPDATE assistant_turns"):
            attempts += 1
            if busy_once and attempts == 1:
                raise OperationalError(statement, parameters, sqlite3.OperationalError("database is locked"))

    event.listen(engine, "before_cursor_execute", contend)
    with factory() as db:
        turn = db.get(AssistantTurn, "turn")
        with factory() as writer:
            writer.execute(text("UPDATE workspaces SET name='UI update' WHERE id='w'"))
            writer.commit()
        store = AssistantTurnStore()
        if operation == "checkpoint":
            store.checkpoint(db, turn, phase="completed", state={"tool_result": "already succeeded"})
        else:
            store.finish(db, turn, status="completed", result={"tool_result": "already succeeded"})
        assert not db.in_transaction()
    with factory() as db:
        saved = db.get(AssistantTurn, "turn")
        assert saved.result_json["keep"] is True
        if operation == "checkpoint":
            assert saved.result_json["checkpoint"] == {"tool_result": "already succeeded"}
        else:
            assert saved.status == "completed"
            assert "checkpoint" not in saved.result_json
    assert attempts == (2 if busy_once else 1)


def test_checkpoint_reserves_writer_before_reading_and_preserves_terminal_state(sessions):
    factory, engine = sessions
    observed = []

    def interleave(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "assistant_turns" in statement:
            with factory() as writer:
                writer.execute(text("PRAGMA busy_timeout=0"))
                with pytest.raises(OperationalError, match="database is locked"):
                    writer.execute(text("UPDATE workspaces SET name='Concurrent' WHERE id='w'"))
                writer.rollback()
            observed.append(True)

    event.listen(engine, "after_cursor_execute", interleave)
    with factory() as db:
        store = AssistantTurnStore()
        store.checkpoint(db, "turn", phase="completed", state={})
        store.finish(db, "turn", status="canceled")
        store.checkpoint(db, "turn", phase="completed", state={"late": True})
    event.remove(engine, "after_cursor_execute", interleave)
    assert observed
    with factory() as db:
        saved = db.get(AssistantTurn, "turn")
        assert saved.status == "canceled"
        assert "checkpoint" not in saved.result_json


def test_checkpoint_refreshes_cached_turn_after_concurrent_cancellation(sessions):
    factory, _engine = sessions
    with factory() as db:
        turn = db.get(AssistantTurn, "turn")
        db.commit()  # expire_on_commit=False leaves the old running row cached.
        with factory() as writer:
            AssistantTurnStore().finish(writer, "turn", status="canceled")
        AssistantTurnStore().checkpoint(db, turn, phase="completed", state={"late": True})
        assert turn.status == "canceled"
        assert "checkpoint" not in turn.result_json
