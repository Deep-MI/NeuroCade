"""Cancellation intent never grants another workflow access to live outputs."""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest
from api_service.jobs.manager import JobManager
from api_service.jobs.reconcile import reconcile_interrupted_runs
from api_service.runtime import neuroimaging_tasks, workflow_runs
from api_service.runtime.run_admission import guard_output_submission
from neurocade_runtime_tools.execution import cancellation_observer
from test_mcp_adapter import database as database

from backend_common.db import Run, RunStatus
from backend_common.run_statuses import run_owns_outputs


def rows(database):
    with database() as db:
        db.add(Run(id="writer", workspace_id="w", created_by_user_id="u", status=RunStatus.running,
                   run_type="test", job_id="writer-job", result_json={"output_ownership": "held"}))
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


@pytest.mark.parametrize("failure", [True, False])
def test_two_workers_cancellation_requires_confirmed_stop(database, monkeypatch, failure):
    rows(database)
    manager = JobManager(concurrency={"fastsurfer": 2})
    monkeypatch.setattr(workflow_runs, "job_manager", manager)
    monkeypatch.setattr(neuroimaging_tasks, "SessionLocal", database)
    entered, release_cancel, stop_writer, running = (threading.Event() for _ in range(4))

    def callback():
        entered.set()
        if failure and not release_cancel.is_set():
            raise RuntimeError("bridge unavailable")
        assert release_cancel.wait(5)
        stop_writer.set()

    def writer():
        observer = cancellation_observer.get()
        assert observer is not None
        observer(callback)
        running.set()
        assert stop_writer.wait(10)
        neuroimaging_tasks._update_run("writer", status=RunStatus.failed,
                                      result={"status": "failed", "return_code": 137, "writer_stopped": True})

    manager.register("writer", writer)
    manager.submit("writer", queue="fastsurfer", job_id="writer-job")
    assert running.wait(5)

    def request():
        with database() as db:
            result = workflow_runs.cancel_workflow_run(db, db.get(Run, "writer"))
            return workflow_runs.cancellation_result(result)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(request)
            assert entered.wait(5)
            assert_admission(database, True)
            with database() as db:
                assert db.get(Run, "writer").status == RunStatus.running
            if failure:
                assert pending.result(timeout=5)["cancellation"] == "unresolved"
                release_cancel.set()
                request()
            else:
                release_cancel.set()
                pending.result(timeout=5)
        handle = manager._handles["writer-job"]
        assert handle.future is not None
        handle.future.result(timeout=5)
        with database() as db:
            writer_run = db.get(Run, "writer")
            assert writer_run.status == RunStatus.canceled
            assert writer_run.result_json["cancellation"] == "stopped"
            assert not run_owns_outputs(writer_run)
        assert_admission(database, False)
    finally:
        release_cancel.set()
        stop_writer.set()
        manager.shutdown(wait=True)


def test_restart_keeps_ownership_until_bridge_confirms_stop(database, monkeypatch):
    rows(database)
    reconcile_interrupted_runs(database)
    assert_admission(database, True)
    manager = JobManager()
    monkeypatch.setattr(workflow_runs, "job_manager", manager)
    from neurocade_runtime_tools.bridge_client import BridgeClient

    class Bridge:
        stopped = False
        def cancel(self, run_id):
            if not self.stopped:
                raise RuntimeError("no acknowledgement")
        def status(self, run_id):
            return {"state": "canceled", "writer_stopped": True}

    bridge = Bridge()
    monkeypatch.setattr(BridgeClient, "from_environment", lambda: bridge)
    with database() as db:
        run = db.get(Run, "writer")
        workflow_runs.cancel_workflow_run(db, run)
        assert run_owns_outputs(run)
    assert_admission(database, True)
    bridge.stopped = True
    with database() as db:
        run = db.get(Run, "writer")
        workflow_runs.cancel_workflow_run(db, run)
        assert not run_owns_outputs(run)
    assert_admission(database, False)

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


def test_integer_exit_code_does_not_release_ownership(database, monkeypatch):
    rows(database)
    monkeypatch.setattr(neuroimaging_tasks, "SessionLocal", database)
    result = {"status": "failed", "return_code": 137}
    neuroimaging_tasks._update_run("writer", status=RunStatus.failed, result=result)
    assert_admission(database, True)
    neuroimaging_tasks._update_run("writer", status=RunStatus.failed, result={**result, "writer_stopped": True})
    assert_admission(database, False)
