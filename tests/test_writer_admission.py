"""One writer at a time, from observable facts that cannot outlive the writer."""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest
from api_service.jobs.manager import JobManager
from api_service.jobs.reconcile import reconcile_interrupted_runs
from api_service.runtime import neuroimaging_tasks, workflow_runs
from api_service.runtime.run_admission import guard_output_submission
from neurocade_runtime_tools.bridge_client import BridgeClient
from test_mcp_adapter import database as database

from backend_common.db import Run, RunStatus
from backend_common.run_statuses import run_is_active


def rows(database, *, status=RunStatus.running):
    # The writer is workspace-scoped: a partial unique index already forbids two
    # queued or running rows on one case, so the interesting collisions are the
    # ones the database cannot reject by itself.
    with database() as db:
        db.add(Run(id="writer", workspace_id="w", created_by_user_id="u", status=status,
                   run_type="test", job_id="writer-job"))
        db.add(Run(id="next", workspace_id="w", case_id="case-w", created_by_user_id="u", status=RunStatus.queued, run_type="test"))
        db.commit()


def assert_admission(database, blocked):
    @guard_output_submission
    def submit(run, workflow):
        return "admitted"

    with database() as db:
        if blocked:
            with pytest.raises(ValueError, match="OUTPUT_BUSY"):
                submit(db.get(Run, "next"), None)
        else:
            assert submit(db.get(Run, "next"), None) == "admitted"


def use_runtime_writers(monkeypatch, run_ids, *, determined=True):
    """Answer the writer probe the way a real bridge would."""
    class Bridge:
        def active_writers(self):
            return determined, set(run_ids)

    monkeypatch.setattr(BridgeClient, "from_environment", classmethod(lambda _cls: Bridge()))


def test_a_scheduled_writer_blocks_a_second_submission(database, monkeypatch):
    """A queued or running row is the schedule's own claim on the case."""
    rows(database)
    use_runtime_writers(monkeypatch, set())

    assert_admission(database, True)

    with database() as db:
        db.get(Run, "writer").status = RunStatus.failed
        db.commit()
    assert_admission(database, False)


def test_a_failure_after_the_container_exits_does_not_strand_the_case(database, monkeypatch):
    """Bookkeeping that fails once must not cost the case its next run.

    The container had already exited when the artifact write failed, so nothing
    is writing and the next submission is admitted. The previous model recorded
    the failure as unproven ownership and blocked the case permanently.
    """
    rows(database)
    monkeypatch.setattr(neuroimaging_tasks, "SessionLocal", database)
    use_runtime_writers(monkeypatch, set())

    neuroimaging_tasks._update_run(
        "writer",
        status=RunStatus.failed,
        result={"status": "failed", "return_code": None, "stderr": "(sqlite3.OperationalError) database is locked"},
        error="(sqlite3.OperationalError) database is locked",
    )

    assert_admission(database, False)


def test_a_surviving_container_blocks_admission_and_then_clears_itself(database, monkeypatch):
    """The runtime, not a stored verdict, decides when the case is free again."""
    rows(database)
    reconcile_interrupted_runs(database)
    with database() as db:
        assert not run_is_active(db.get(Run, "writer"))

    use_runtime_writers(monkeypatch, {"writer"})
    assert_admission(database, True)

    # The container exits. No operator action, no database edit.
    use_runtime_writers(monkeypatch, set())
    assert_admission(database, False)


def test_an_unreachable_bridge_still_honours_the_schedule(database, monkeypatch):
    """An unanswerable probe narrows protection; it never blocks forever."""
    rows(database)

    def unreachable(_cls):
        raise RuntimeError("Runtime bridge is unavailable")

    monkeypatch.setattr(BridgeClient, "from_environment", classmethod(unreachable))

    assert_admission(database, True)
    with database() as db:
        db.get(Run, "writer").status = RunStatus.completed
        db.commit()
    assert_admission(database, False)


def test_cancelling_an_orphaned_run_finalizes_it_without_bridge_proof(database, monkeypatch):
    """A job the manager no longer knows has no callback left to report."""
    rows(database)
    monkeypatch.setattr(workflow_runs, "job_manager", JobManager())
    use_runtime_writers(monkeypatch, set())

    def unreachable(_cls):
        raise RuntimeError("Runtime bridge is unavailable")

    monkeypatch.setattr(BridgeClient, "from_environment", classmethod(unreachable))

    with database() as db:
        result = workflow_runs.cancel_workflow_run(db, db.get(Run, "writer"))
        assert result.status == RunStatus.canceled
        assert result.result_json["cancellation"] == "stopped"

    use_runtime_writers(monkeypatch, set())
    assert_admission(database, False)


def test_a_live_worker_keeps_the_case_until_it_writes_the_terminal_row(database, monkeypatch):
    """Accepting a cancel request is still not the end of the run."""
    rows(database)
    manager = JobManager(concurrency={"fastsurfer": 2})
    monkeypatch.setattr(workflow_runs, "job_manager", manager)
    monkeypatch.setattr(neuroimaging_tasks, "SessionLocal", database)
    use_runtime_writers(monkeypatch, set())
    stop_writer, running = threading.Event(), threading.Event()

    def writer():
        running.set()
        assert stop_writer.wait(10)
        neuroimaging_tasks._update_run("writer", status=RunStatus.failed,
                                       result={"status": "failed", "return_code": 137})

    manager.register("writer", writer)
    manager.submit("writer", queue="fastsurfer", job_id="writer-job")
    assert running.wait(5)
    try:
        with database() as db:
            workflow_runs.cancel_workflow_run(db, db.get(Run, "writer"))
        with database() as db:
            assert db.get(Run, "writer").status == RunStatus.running
        assert_admission(database, True)

        stop_writer.set()
        handle = manager._handles["writer-job"]
        assert handle.future is not None
        handle.future.result(timeout=5)
        with database() as db:
            writer_run = db.get(Run, "writer")
            assert writer_run.status == RunStatus.canceled
            assert writer_run.result_json["cancellation"] == "stopped"
        assert_admission(database, False)
    finally:
        stop_writer.set()
        manager.shutdown(wait=True)


@pytest.mark.parametrize("failure", [True, False])
def test_bridge_does_not_publish_canceled_before_stop(monkeypatch, failure):
    from types import SimpleNamespace

    from neurocade_runtime_tools import bridge as bridge_module
    from neurocade_runtime_tools.bridge import BridgeRuntime, RunRecord
    from neurocade_runtime_tools.protocol import RunState

    class Process:
        returncode = None
        def poll(self):
            return self.returncode

    process = Process()
    record = RunRecord(run_id="run", request_hash="hash", backend="docker", process=cast(subprocess.Popen[Any], process),
                       state=RunState.running, docker_name="container")
    runtime = BridgeRuntime.__new__(BridgeRuntime)
    runtime._lock = threading.Lock()
    runtime._runs = {"run": record}
    runtime.terminal_ttl_s = 3600
    entered, release = threading.Event(), threading.Event()

    def remove(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if failure:
            raise RuntimeError("bridge could not remove container")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bridge_module, "run_managed_command", remove)
    monkeypatch.setattr(bridge_module, "_terminate_process_group", lambda process: setattr(process, "returncode", -15))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.cancel, "run")
        try:
            assert entered.wait(5)
            assert record.public()["state"] == "running"
            assert record.public()["cancellation"] == "requested"
        finally:
            release.set()
        if failure:
            with pytest.raises(RuntimeError):
                pending.result(timeout=5)
            assert record.public()["state"] == "running"
            assert process.returncode is None
        else:
            pending.result(timeout=5)
            assert record.public()["state"] == "canceled"
            assert process.returncode == -15


@pytest.mark.parametrize("timed_out", [True, False])
@pytest.mark.parametrize("confirmed", [True, False])
def test_watcher_requires_daemon_stop_proof(monkeypatch, timed_out, confirmed):
    from types import SimpleNamespace

    from neurocade_runtime_tools import bridge as module
    from neurocade_runtime_tools.bridge import BridgeRuntime, RunRecord
    from neurocade_runtime_tools.protocol import RunState

    class Process:
        returncode = 17
        waited = False
        def wait(self, timeout=None):
            if timed_out and not self.waited:
                self.waited = True
                raise subprocess.TimeoutExpired("docker run", timeout or 1)
            return self.returncode
        def poll(self):
            return self.returncode

    process = Process()
    record = RunRecord(run_id="run", request_hash="hash", backend="docker",
                       process=cast(subprocess.Popen[Any], process), state=RunState.running, docker_name="writer")
    runtime = BridgeRuntime.__new__(BridgeRuntime)
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "rm":
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="" if confirmed else "still-running-container")
    monkeypatch.setattr(module, "run_managed_command", command)
    monkeypatch.setattr(module, "_terminate_process_group", lambda process: setattr(process, "returncode", -9))
    runtime._watch(record, 1, None, None, None, None, [])
    assert [call[1] for call in calls] == ["rm", "container"]
    assert record.public()["writer_stopped"] is confirmed
    assert record.public()["state"] == (("timed_out" if timed_out else "failed") if confirmed else "running")
