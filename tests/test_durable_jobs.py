"""Durable background-job persistence and restart recovery tests."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.jobs.manager import JobManager  # noqa: E402
from api_service.jobs.reconcile import reconcile_interrupted_runs  # noqa: E402
from api_service.jobs.store import DurableJobStore  # noqa: E402

from backend_common.db import (  # noqa: E402
    BackgroundJob,
    Base,
    Case,
    RoleEnum,
    Run,
    RunStatus,
    User,
    Workspace,
    WorkspaceMembership,
)


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite'}", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _await_ready(manager: JobManager, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status(task_id)
        if status["ready"]:
            return status
        time.sleep(0.01)
    raise AssertionError(f"job {task_id} did not finish")


def test_job_submission_and_result_are_persisted(tmp_path):
    session_factory = _session_factory(tmp_path)
    manager = JobManager(
        concurrency={"api": 1},
        durable_store=DurableJobStore(session_factory),
    )
    manager.register("add", lambda left, right: {"sum": left + right})

    task_id = manager.submit("add", {"left": 2, "right": 3})
    status = _await_ready(manager, task_id)

    assert status["result"] == {"sum": 5}
    with session_factory() as db:
        row = db.get(BackgroundJob, task_id)
        assert row is not None
        assert row.state == "completed"
        assert row.kwargs_json == {"left": 2, "right": 3}
        assert row.result_json == {"sum": 5}
        assert row.started_at is not None
        assert row.finished_at is not None
    manager.shutdown(wait=True)


def test_recover_pending_requeues_durable_queued_job(tmp_path):
    session_factory = _session_factory(tmp_path)
    store = DurableJobStore(session_factory)
    store.create(
        job_id="queued-job",
        task_name="echo",
        queue_name="api",
        kwargs={"value": "restored"},
    )
    manager = JobManager(concurrency={"api": 1}, durable_store=store)
    manager.register("echo", lambda value: {"value": value})

    recovered = manager.recover_pending()
    status = _await_ready(manager, "queued-job")

    assert recovered == {"queued-job"}
    assert status["result"] == {"value": "restored"}
    manager.shutdown(wait=True)


def test_recover_pending_marks_running_job_interrupted(tmp_path):
    session_factory = _session_factory(tmp_path)
    store = DurableJobStore(session_factory)
    store.create(
        job_id="running-job",
        task_name="echo",
        queue_name="api",
        kwargs={"value": "not-resumable"},
    )
    assert store.mark_running("running-job")
    manager = JobManager(concurrency={"api": 1}, durable_store=store)
    manager.register("echo", lambda value: value)

    recovered = manager.recover_pending()

    assert recovered == set()
    status = manager.status("running-job")
    assert status["status"] == "failed"
    assert status["error"] == "Interrupted by an application restart."


def test_run_reconciliation_preserves_recovered_queued_job(tmp_path):
    session_factory = _session_factory(tmp_path)
    with session_factory() as db:
        user = User(id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(id="workspace-1", owner_user_id=user.id, name="workspace-1")
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=RoleEnum.owner,
            granted_by_user_id=user.id,
        )
        case = Case(
            id="case-a-id",
            workspace_id=workspace.id,
            owner_user_id=user.id,
            title="case-a",
        )
        run = Run(
            id="run-1",
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            status=RunStatus.queued,
            run_type="fastsurfer_full",
            job_id="queued-job",
        )
        db.add_all([user, workspace, membership, case, run])
        db.commit()

    reconciled = reconcile_interrupted_runs(
        session_factory,
        recovered_job_ids={"queued-job"},
    )

    assert reconciled == 0
    with session_factory() as db:
        run = db.get(Run, "run-1")
        assert run is not None and run.status == RunStatus.queued
