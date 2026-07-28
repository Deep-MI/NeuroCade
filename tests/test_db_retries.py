"""Test bounded SQLite lock retry behavior."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from backend_common import db as db_module


class FakeSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1


def _operational_error(message: str) -> OperationalError:
    return OperationalError("UPDATE example SET value = 1", {}, sqlite3.OperationalError(message))


def test_sqlite_lock_retry_retries_complete_operation(monkeypatch) -> None:
    session = FakeSession()
    calls = 0
    delays: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _operational_error("database is locked")
        return "done"

    monkeypatch.setattr(db_module.time, "sleep", delays.append)

    result = db_module.run_with_sqlite_lock_retry(
        session,  # type: ignore[arg-type]
        operation,
        attempts=3,
        base_delay_seconds=0.1,
    )

    assert result == "done"
    assert calls == 3
    assert session.rollback_count == 2
    assert delays == [0.1, 0.2]


def test_sqlite_lock_retry_does_not_retry_other_operational_errors(monkeypatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(db_module.time, "sleep", lambda _delay: pytest.fail("unexpected retry"))

    with pytest.raises(OperationalError, match="disk I/O error"):
        db_module.run_with_sqlite_lock_retry(
            session,  # type: ignore[arg-type]
            lambda: (_ for _ in ()).throw(_operational_error("disk I/O error")),
        )

    assert session.rollback_count == 1
