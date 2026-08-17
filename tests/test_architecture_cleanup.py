"""Regression coverage for the architecture cleanup checklist."""

from __future__ import annotations

import asyncio
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import helpers as helpers_module  # noqa: E402
from api_service.cases import uploads as uploads_module  # noqa: E402
from api_service.cases.run_operations import cancel_active_case_run  # noqa: E402
from api_service.runtime import gui_state as gui_state_module  # noqa: E402
from api_service.runtime import neuroimaging_tasks as neuroimaging_tasks_module  # noqa: E402
from api_service.runtime import workflow_runs as workflow_runs_module  # noqa: E402
from api_service.runtime.gui_state import GuiStateStore  # noqa: E402
from api_service.schemas import AssistantTurnRequest  # noqa: E402

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.db import Base, Case, RoleEnum, Run, RunStatus, User, Workspace, WorkspaceMembership  # noqa: E402
from backend_common.storage_transactions import (  # noqa: E402
    finalize_staged_path,
    restore_staged_path,
    stage_path_for_deletion,
)


def test_assistant_turn_request_accepts_only_one_bounded_user_turn():
    payload = AssistantTurnRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "Inspect this case."}],
            "workspace_id": "workspace-1",
            "gui_session_id": "gui-1",
        }
    )
    assert payload.messages[0].content == "Inspect this case."

    with pytest.raises(ValidationError):
        AssistantTurnRequest.model_validate(
            {
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": "second"},
                ],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-1",
            }
        )
    with pytest.raises(ValidationError):
        AssistantTurnRequest.model_validate(
            {
                "messages": [{"role": "assistant", "content": "caller history"}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-1",
            }
        )
    with pytest.raises(ValidationError):
        AssistantTurnRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "x" * 20_001}],
                "workspace_id": "workspace-1",
                "gui_session_id": "gui-1",
            }
        )


def test_gui_state_store_expires_idle_sessions(monkeypatch):
    now = 100.0
    monkeypatch.setattr(gui_state_module.time, "monotonic", lambda: now)
    store = GuiStateStore(ttl_seconds=10, max_entries=4)
    store.state_for_key("old")["case_id"] = "case-a"

    now = 111.0
    assert store.state_for_key("old")["case_id"] is None


def test_gui_state_store_evicts_least_recently_used_session(monkeypatch):
    now = 100.0
    monkeypatch.setattr(gui_state_module.time, "monotonic", lambda: now)
    store = GuiStateStore(ttl_seconds=100, max_entries=2)
    store.state_for_key("oldest")["case_id"] = "case-a"
    now = 101.0
    store.state_for_key("newer")["case_id"] = "case-b"
    now = 102.0
    store.state_for_key("newest")

    assert store.state_for_key("newer")["case_id"] == "case-b"
    assert store.state_for_key("oldest")["case_id"] is None


def test_cancel_uses_persisted_run_job_id(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    output_dir = data_root / "output"
    case_dir = output_dir / "workspaces" / "workspace-1" / "cases" / "case-a"
    case_dir.mkdir(parents=True)
    (case_dir.parent.parent / ".neurocade-workspace.json").write_text(
        json.dumps({"id": "workspace-1"}), encoding="utf-8"
    )
    (case_dir / ".neurocade-case.json").write_text(
        json.dumps({"id": "case-a-id"}), encoding="utf-8"
    )
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
    workspace = Workspace(id="workspace-1", owner_user_id=user.id, name="workspace-1")
    case = Case(id="case-a-id", workspace_id=workspace.id, owner_user_id=user.id, title="case-a")
    run = Run(
        id="run-1",
        case_id=case.id,
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        status=RunStatus.running,
        run_type="fastsurfer_fast",
        job_id="durable-job-id",
    )
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=RoleEnum.owner,
        granted_by_user_id=user.id,
    )
    db.add_all([user, workspace, case, run, membership])
    db.commit()
    monkeypatch.setattr(helpers_module.settings, "fs_data_root", data_root)
    canceled: list[str] = []
    monkeypatch.setattr(workflow_runs_module.job_manager, "cancel", lambda job_id: bool(canceled.append(job_id)))

    result = cancel_active_case_run(
        db,
        AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"),
        case_id=case.id,
    )

    assert canceled == ["durable-job-id"]
    assert result["status"] == "canceled"
    canceled_run = db.get(Run, run.id)
    assert canceled_run is not None and canceled_run.status == RunStatus.canceled


def test_neuroimaging_worker_does_not_overwrite_canceled_run(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'worker.sqlite'}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with session_factory() as db:
        user = User(id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(id="workspace-1", owner_user_id=user.id, name="workspace-1")
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
            run_type="fastsurfer_fast",
        )
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=RoleEnum.owner,
            granted_by_user_id=user.id,
        )
        db.add_all([user, workspace, membership, case, run])
        db.commit()

    monkeypatch.setattr(neuroimaging_tasks_module, "SessionLocal", session_factory)
    case_dir = tmp_path / "output"
    case_dir.mkdir()
    (case_dir / "input.mgz").write_bytes(b"input")

    execution_paths: dict[str, Path] = {}

    def fake_execute(*_args, **kwargs):
        execution_paths["stdout"] = kwargs["stdout_path"]
        execution_paths["stderr"] = kwargs["stderr_path"]
        with session_factory() as db:
            run = db.get(Run, "run-1")
            assert run is not None and run.status == RunStatus.running
            run.status = RunStatus.canceled
            db.commit()
        return {
            "status": "failed",
            "run_id": "run-1",
            "tool_id": "fastsurfer_fast",
            "return_code": -15,
            "outputs": [{"name": "conformed_input", "state": "created"}],
        }

    monkeypatch.setattr(neuroimaging_tasks_module, "execute_workflow", fake_execute)
    result = neuroimaging_tasks_module.run_neuroimaging_workflow_task(
        run_id="run-1",
        tool_id="fastsurfer_fast",
        inputs=["/case/input.mgz"],
        bind_host_path=str(case_dir),
        bind_container_path="/case",
        case_id="case-a-id",
        gpu_enabled=False,
    )

    with session_factory() as db:
        run = db.get(Run, "run-1")
        assert run is not None and run.status == RunStatus.canceled
    assert result["status"] == "canceled"
    assert execution_paths == {
        "stdout": case_dir / "scripts" / "runs" / "run-1" / "stdout.log",
        "stderr": case_dir / "scripts" / "runs" / "run-1" / "stderr.log",
    }
    assert run.result_json == {
        "status": "canceled",
        "run_id": "run-1",
        "tool_id": "fastsurfer_fast",
        "outputs": [{"name": "conformed_input", "state": "created"}],
    }


def test_neuroimaging_worker_records_startup_failure(monkeypatch, tmp_path):
    """A failed running transition must not leave the durable run queued."""
    transitions = []

    def update(_run_id, *, status, result, error=None):
        transitions.append((status, result, error))
        if status == RunStatus.running:
            raise RuntimeError("database is locked")
        return True

    monkeypatch.setattr(neuroimaging_tasks_module, "_update_run", update)
    result = neuroimaging_tasks_module.run_neuroimaging_workflow_task(
        run_id="run-1",
        tool_id="fastsurfer_fast",
        inputs=["/case/input.mgz"],
        bind_host_path=str(tmp_path),
        bind_container_path="/case",
        case_id="case-1",
        gpu_enabled=False,
    )

    assert result["status"] == "failed"
    assert [transition[0] for transition in transitions] == [RunStatus.running, RunStatus.failed]
    assert transitions[-1][2] == "Failed to start workflow: database is locked"


def test_staged_storage_can_be_restored_or_finalized(tmp_path):
    original = tmp_path / "workspace" / "case"
    original.mkdir(parents=True)
    (original / "scan.mgz").write_bytes(b"scan")
    staged = stage_path_for_deletion(original, tmp_path / "trash")

    assert staged is not None
    assert not original.exists()
    restore_staged_path(staged)
    assert (original / "scan.mgz").read_bytes() == b"scan"

    staged_again = stage_path_for_deletion(original, tmp_path / "trash")
    finalize_staged_path(staged_again)
    assert staged_again is not None
    assert not staged_again.staged_path.exists()


def test_chunked_upload_rejects_oversized_and_removes_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr(uploads_module.settings, "max_upload_file_size_bytes", 4)
    target = tmp_path / "generated.nii"
    upload = UploadFile(filename="generated.nii", file=BytesIO(b"12345"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(uploads_module._write_upload_file(upload, target))

    assert exc_info.value.status_code == 413
    assert not target.exists()
