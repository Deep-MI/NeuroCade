"""SQLite-backed persistence for the in-process background job manager."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from backend_common.db import BackgroundJob, run_with_sqlite_lock_retry

TERMINAL_JOB_STATES = frozenset({"completed", "failed", "canceled"})
ACTIVE_JOB_STATES = frozenset({"queued", "running"})
MAX_JOB_PAYLOAD_CHARACTERS = 1_000_000


@dataclass(frozen=True)
class StoredJob:
    """Serializable job data needed to restore a queued submission."""

    id: str
    task_name: str
    queue_name: str
    state: str
    kwargs: dict[str, Any]


def _validated_json(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    if len(encoded) > MAX_JOB_PAYLOAD_CHARACTERS:
        raise ValueError(f"{label} exceeds the {MAX_JOB_PAYLOAD_CHARACTERS:,} character limit")
    return json.loads(encoded)


def _bounded_result(value: Any) -> Any:
    try:
        return _validated_json(value, label="Job result")
    except ValueError:
        preview = str(value)
        return {
            "truncated": True,
            "preview": preview[:10_000],
        }


class DurableJobStore:
    """Persist job submissions and lifecycle transitions in the application DB."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(self, *, job_id: str, task_name: str, queue_name: str, kwargs: dict[str, Any]) -> None:
        validated_kwargs = _validated_json(kwargs, label="Job arguments")
        with self._session_factory() as db:

            def operation() -> None:
                db.add(
                    BackgroundJob(
                        id=job_id,
                        task_name=task_name,
                        queue_name=queue_name,
                        state="queued",
                        kwargs_json=validated_kwargs,
                    )
                )
                db.commit()

            run_with_sqlite_lock_retry(db, operation)

    def mark_running(self, job_id: str) -> bool:
        with self._session_factory() as db:

            def operation() -> bool:
                updated = (
                    db.query(BackgroundJob)
                    .filter(
                        BackgroundJob.id == job_id,
                        BackgroundJob.state == "queued",
                    )
                    .update(
                        {
                            BackgroundJob.state: "running",
                            BackgroundJob.started_at: datetime.now(UTC),
                            BackgroundJob.error_message: None,
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
                return bool(updated)

            return run_with_sqlite_lock_retry(db, operation)

    def mark_terminal(self, job_id: str, *, state: str, result: Any = None, error: str | None = None) -> bool:
        if state not in TERMINAL_JOB_STATES:
            raise ValueError(f"Invalid terminal job state: {state}")
        with self._session_factory() as db:

            def operation() -> bool:
                updated = (
                    db.query(BackgroundJob)
                    .filter(
                        BackgroundJob.id == job_id,
                        BackgroundJob.state.in_(ACTIVE_JOB_STATES),
                    )
                    .update(
                        {
                            BackgroundJob.state: state,
                            BackgroundJob.result_json: _bounded_result(result) if result is not None else None,
                            BackgroundJob.error_message: error,
                            BackgroundJob.finished_at: datetime.now(UTC),
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
                return bool(updated)

            return run_with_sqlite_lock_retry(db, operation)

    def cancel(self, job_id: str) -> bool:
        return self.mark_terminal(job_id, state="canceled")

    def active_jobs(self) -> list[StoredJob]:
        with self._session_factory() as db:
            rows = (
                db.query(BackgroundJob)
                .filter(BackgroundJob.state.in_(ACTIVE_JOB_STATES))
                .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
                .all()
            )
            return [
                StoredJob(
                    id=row.id,
                    task_name=row.task_name,
                    queue_name=row.queue_name,
                    state=row.state,
                    kwargs=dict(row.kwargs_json or {}),
                )
                for row in rows
            ]

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            row = db.get(BackgroundJob, job_id)
            if row is None:
                return None
            ready = row.state in TERMINAL_JOB_STATES
            return {
                "job_id": row.id,
                "status": row.state,
                "ready": ready,
                "result": row.result_json if ready else None,
                "error": row.error_message,
            }

    def queue_status(self, queue_names: set[str] | None = None) -> dict[str, int]:
        with self._session_factory() as db:
            query = db.query(BackgroundJob.state).filter(BackgroundJob.state.in_(ACTIVE_JOB_STATES))
            if queue_names is not None:
                query = query.filter(BackgroundJob.queue_name.in_(queue_names))
            states = [state for (state,) in query.all()]
        active = states.count("running")
        queued = states.count("queued")
        return {"active": active, "queued": queued, "total": active + queued}

    def prune_terminal(self, *, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=max(retention_days, 1))
        with self._session_factory() as db:

            def operation() -> int:
                deleted = (
                    db.query(BackgroundJob)
                    .filter(
                        BackgroundJob.state.in_(TERMINAL_JOB_STATES),
                        BackgroundJob.finished_at < cutoff,
                    )
                    .delete(synchronize_session=False)
                )
                db.commit()
                return int(deleted)

            return run_with_sqlite_lock_retry(db, operation)
