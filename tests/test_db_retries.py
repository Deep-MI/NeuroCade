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


@pytest.mark.parametrize("message", ["disk I/O error", "file is not a database"])
def test_sqlite_storage_error_classification(message: str) -> None:
    assert db_module.is_sqlite_storage_error(_operational_error(message))


def test_sqlite_storage_error_rejects_lock_contention() -> None:
    assert not db_module.is_sqlite_storage_error(_operational_error("database is locked"))


def test_workflow_output_indexing_retries_write_lock_contention(monkeypatch) -> None:
    """A finished workflow must not be reported as failed over a busy database.

    Output indexing runs after the container has already exited. Letting a
    transient write lock escape turned completed runs into failed ones.
    """
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api-service"))
    from api_service.runtime_tools import workflow_execution

    class RecordingSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.commits = 0

        def get(self, _model, _identity):  # noqa: ANN001, ANN202
            return SimpleNamespace(id="case-1")

        def commit(self) -> None:
            self.commits += 1

    session = RecordingSession()
    attempts = 0

    def flaky_index(*_args, **_kwargs) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _operational_error("database is locked")

    monkeypatch.setattr(workflow_execution, "index_workflow_outputs", flaky_index)
    monkeypatch.setattr(db_module.time, "sleep", lambda _seconds: None)

    workflow_execution._index_output_records(
        SimpleNamespace(tool=SimpleNamespace(id="fastsurfer_full"), run_id="run-1"),
        [{"name": "output", "state": "created"}],
        "case-1",
        session,  # type: ignore[arg-type]
    )

    assert attempts == 2
    assert session.rollback_count == 1
    assert session.commits == 1
