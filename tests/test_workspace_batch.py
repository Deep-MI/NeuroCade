"""Test workspace batch behavior for NeuroCade."""

import asyncio
import json
from pathlib import Path
import sys
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.workspace_batch import service as workspace_batch_module  # noqa: E402
from api_service.workspace_batch import runner as workspace_batch_runner_module  # noqa: E402
from api_service.workspace_batch import filesystem as workspace_batch_fs_module  # noqa: E402
from api_service.workspace_batch import reports as workspace_batch_reports_module  # noqa: E402
from backend_common import db as backend_db_module  # noqa: E402
from backend_common.db import (  # noqa: E402
    Artifact,
    ArtifactKind,
    Base,
    RunStatus,
    Run,
)
from backend_common.case_storage import workspace_analysis_dir  # noqa: E402
from neurocade_runtime_tools.execution import RuntimeExecutionRequest, RuntimeExecutionResult  # noqa: E402
from tests.factories import seed_workspace_context  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    """Create an isolated SQLite session for workspace batch tests."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'workspace_batch.sqlite3'}", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_context(db_session, tmp_path, monkeypatch):
    """Seed a workspace with two cases and patched data roots."""
    context, workspace, cases = seed_workspace_context(
        db_session,
        workspace_id="workspace-1",
        case_specs=(("case-a", "case-a"), ("case-b", "case-b")),
    )
    fs_data_root = tmp_path / "neurocade-data"
    monkeypatch.setattr(workspace_batch_module.settings, "fs_data_root", fs_data_root)
    monkeypatch.setattr(workspace_batch_module.settings, "outputs_dir_override", fs_data_root / "output")
    return db_session, context, workspace, cases[0], cases[1]


def test_create_workspace_batch_run_creates_probe_and_manifest(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    queued = []

    def fake_queue(run_id: str, case_id: str, *, is_probe: bool) -> str:
        queued.append((run_id, case_id, is_probe))
        return f"task-{case_id}"

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_batch_case", fake_queue)

    summary = workspace_batch_module.create_workspace_batch_run(
        db_session,
        context,
        workspace,
        command="mri_synthstrip --help | head",
        report_name="synthstrip-batch",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )

    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()
    runs = db_session.query(Run).filter(Run.parent_run_id == summary.run_id).order_by(Run.created_at.asc()).all()

    assert summary.status == "queued"
    assert summary.total_cases == 2
    assert queued == [(summary.run_id, case_a.id, True)]
    assert workflow.runtime_job_id is not None
    assert len(runs) == 2
    assert runs[0].external_task_id == f"task-{case_a.id}"
    assert runs[1].external_task_id is None
    assert db_session.query(Artifact).filter(
        Artifact.workspace_id == workspace.id,
        Artifact.case_id.is_(None),
    ).count() == 3


def test_create_workspace_batch_run_commits_before_probe_enqueue(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False)

    queued = []

    def fake_queue(run_id: str, case_id: str, *, is_probe: bool) -> str:
        with session_factory() as verification_session:
            workflow = verification_session.query(Run).filter(Run.id == run_id).one_or_none()
            run = (
                verification_session.query(Run)
                .filter(Run.parent_run_id == run_id, Run.case_id == case_id)
                .one_or_none()
            )
            assert workflow is not None
            assert run is not None
        queued.append((run_id, case_id, is_probe))
        return f"task-{case_id}"

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_batch_case", fake_queue)

    summary = workspace_batch_module.create_workspace_batch_run(
        db_session,
        context,
        workspace,
        command="mri_info /case/orig.mgz",
        report_name="commit-check",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )

    assert queued == [(summary.run_id, case_a.id, True)]


def test_create_workspace_batch_run_rejects_active_selected_case(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    db_session.add(
        Run(
            case_id=case_a.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.running,
            run_type="run_fastsurfer",
            runtime_job_id=case_a.id,
            result_json={},
        )
    )
    db_session.commit()
    monkeypatch.setattr(workspace_batch_module, "queue_workspace_batch_case", lambda *_args, **_kwargs: "task-should-not-queue")

    with pytest.raises(HTTPException) as exc_info:
        workspace_batch_module.create_workspace_batch_run(
            db_session,
            context,
            workspace,
            command="mri_info /case/orig.mgz",
            report_name="active-check",
            case_ids=[case_a.id, case_b.id],
            thread_id="workspace:workspace-1",
            provider_name="openai-compatible",
            model_name="qwen",
        )

    assert exc_info.value.status_code == 409


def test_workspace_probe_bash_targets_selected_case(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context

    class FakeRuntimeService:
        async def run_workspace_case_command(self, *, command: str, case_dir: str, db=None, artifact_index_targets=()) -> str:
            assert command == "mri_info /case/orig.mgz"
            assert case_dir == str(workspace_batch_module.case_storage_dir(workspace_batch_module.settings, workspace.id, case_a.id).resolve())
            assert db is db_session
            assert artifact_index_targets
            assert artifact_index_targets[0].case_id == case_a.id
            return "ok"

    monkeypatch.setattr(workspace_batch_module, "_runtime_service", FakeRuntimeService())

    result = asyncio.run(
        workspace_batch_module.workspace_probe_bash(
            db_session,
            context,
            workspace,
            command="mri_info /case/orig.mgz",
            case_id=case_a.id,
        )
    )

    assert "Workspace probe command ran on case `case-a`" in result


def test_workspace_file_tree_uses_workspace_case_mounts(seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    tree = workspace_batch_fs_module.workspace_file_tree(
        db_session,
        context,
        workspace,
        case_ids=[case_a.id, case_b.id],
    )

    assert "/cases/case-a" in tree
    assert "/cases/case-b" in tree
    assert "/workspace/" in tree


def test_create_workspace_command_run_queues_single_workspace_task(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    queued = []

    def fake_queue(run_id: str) -> str:
        queued.append(run_id)
        return "task-workspace-1"

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_command_run", fake_queue)

    summary = workspace_batch_module.create_workspace_command_run(
        db_session,
        context,
        workspace,
        command="python summarize.py --input /cases/case-a/orig.mgz --out /workspace/report.csv",
        report_name="workspace-summary",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )

    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()

    assert queued == [summary.run_id]
    assert summary.run_type == workspace_batch_module.WORKSPACE_COMMAND_ACTION
    assert summary.selected_case_count == 2
    assert summary.external_task_id == "task-workspace-1"
    assert workflow.run_type == workspace_batch_module.WORKSPACE_COMMAND_ACTION
    assert (workflow.result_json or {}).get("external_task_id") == "task-workspace-1"


@pytest.mark.parametrize("queue_kind", ["case", "workspace"])
def test_workspace_queue_uses_runtime_submission(monkeypatch, seeded_context, queue_kind):
    db_session, context, workspace, case_a, case_b = seeded_context
    is_case_queue = queue_kind == "case"
    workflow = workspace_batch_module._new_workspace_run(
        context,
        workspace,
        run_type=workspace_batch_module.WORKSPACE_BATCH_ACTION if is_case_queue else workspace_batch_module.WORKSPACE_COMMAND_ACTION,
        command="mri_info /case/orig.mgz" if is_case_queue else "find /cases -type f > /workspace/files.txt",
        report_name="batch" if is_case_queue else "workspace-command",
        default_report_name="workspace-batch" if is_case_queue else "workspace-command",
        selected_cases=[case_a] if is_case_queue else [case_a, case_b],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    db_session.add(workflow)
    db_session.commit()
    captured: dict[str, object] = {}

    class SessionContext:
        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_submit_runtime_request(task, request, *, kwargs=None):
        captured["task"] = task
        captured["request"] = request
        captured["kwargs"] = kwargs
        task_id = "task-case-1" if is_case_queue else "task-workspace-1"
        request.task_id = task_id
        return RuntimeExecutionResult(request=request, returncode=0, submitted_task_id=task_id)

    monkeypatch.setattr(workspace_batch_module, "SessionLocal", lambda: SessionContext())
    monkeypatch.setattr(workspace_batch_module, "submit_runtime_request", fake_submit_runtime_request)

    if is_case_queue:
        task_id = workspace_batch_module.queue_workspace_batch_case(workflow.id, case_a.id, is_probe=True)
    else:
        task_id = workspace_batch_module.queue_workspace_command_run(workflow.id)

    request = cast(RuntimeExecutionRequest, captured["request"])
    assert task_id == ("task-case-1" if is_case_queue else "task-workspace-1")
    assert request.synchronous is False
    assert request.execution_mode == "job-submit"
    assert request.queue_name == workspace_batch_module.WORKSPACE_BATCH_QUEUE
    assert request.user_id == context.user.id
    assert request.workspace_id == workspace.id
    # A job id is pre-generated and threaded into kwargs so the runner can
    # persist it as external_task_id; it is a UUID string.
    job_kwargs = cast(dict, captured["kwargs"])
    assert isinstance(job_kwargs["task_id"], str) and job_kwargs["task_id"]
    if is_case_queue:
        assert request.case_id == case_a.id
        assert request.artifact_index_targets
        assert request.artifact_index_targets[0].case_id == case_a.id
        assert {k: v for k, v in job_kwargs.items() if k != "task_id"} == {
            "run_id": workflow.id,
            "case_id": case_a.id,
            "is_probe": True,
        }
    else:
        assert request.workspace_artifact_sync_targets
        assert request.workspace_artifact_sync_targets[0].run_id == workflow.id
        assert {k: v for k, v in job_kwargs.items() if k != "task_id"} == {"run_id": workflow.id}


def test_process_workspace_command_run_passes_runtime_metadata(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_command_run", lambda run_id: "task-workspace-1")
    summary = workspace_batch_module.create_workspace_command_run(
        db_session,
        context,
        workspace,
        command="find /cases -maxdepth 2 -type f > /workspace/files.txt",
        report_name="workspace-summary",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()
    analysis_id = workspace_batch_module._analysis_id_from_run(workflow)
    expected_analysis_dir = workspace_analysis_dir(workspace_batch_module.settings, workspace.id, analysis_id)
    captured: dict[str, object] = {}

    class SessionContext:
        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRuntimeService:
        async def run_workspace_command(
            self,
            *,
            command: str,
            cases_dir: str,
            workspace_dir: str,
            db=None,
            workspace_artifact_sync_targets=(),
            queue_name=None,
            task_id=None,
        ) -> str:
            captured["command"] = command
            captured["cases_dir"] = cases_dir
            captured["workspace_dir"] = workspace_dir
            captured["db"] = db
            captured["workspace_artifact_sync_targets"] = workspace_artifact_sync_targets
            captured["queue_name"] = queue_name
            captured["task_id"] = task_id
            return "ok"

    monkeypatch.setattr(backend_db_module, "SessionLocal", lambda: SessionContext())
    monkeypatch.setattr(workspace_batch_runner_module, "_runtime_service", FakeRuntimeService())

    workspace_batch_runner_module.process_workspace_command_run(summary.run_id, task_id="task-workspace-1")

    targets = captured["workspace_artifact_sync_targets"]
    assert captured["command"] == "find /cases -maxdepth 2 -type f > /workspace/files.txt"
    assert captured["cases_dir"] == str((workspace_batch_module.settings.fs_data_root / ".workspace-inputs" / analysis_id / "cases").resolve())
    assert captured["workspace_dir"] == str(expected_analysis_dir.resolve())
    assert captured["db"] is db_session
    assert captured["queue_name"] == workspace_batch_module.WORKSPACE_BATCH_QUEUE
    assert captured["task_id"] == "task-workspace-1"
    assert targets == ()


def test_workspace_command_artifact_sync_is_idempotent(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_command_run", lambda run_id: "task-workspace-1")
    summary = workspace_batch_module.create_workspace_command_run(
        db_session,
        context,
        workspace,
        command="find /cases -maxdepth 2 -type f > /workspace/files.txt",
        report_name="workspace-summary",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()

    analysis_id = workspace_batch_module._analysis_id_from_run(workflow)
    workspace_batch_fs_module.prepare_workspace_command_inputs(
        db_session,
        workflow,
        workspace,
        analysis_id=analysis_id,
    )
    workspace_batch_module.complete_workspace_run_files(db_session, workflow)
    db_session.commit()
    workspace_batch_module.complete_workspace_run_files(db_session, workflow)
    db_session.commit()

    cases_json_artifacts = [
        artifact
        for artifact in db_session.query(Artifact).all()
        if artifact.relative_path.endswith("/cases.json") and (artifact.metadata_json or {}).get("run_id") == summary.run_id
    ]
    assert len(cases_json_artifacts) == 1


def test_workspace_run_artifact_listing_ignores_unrelated_workspace_artifacts(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_command_run", lambda run_id: "task-workspace-1")
    summary = workspace_batch_module.create_workspace_command_run(
        db_session,
        context,
        workspace,
        command="find /cases -maxdepth 2 -type f > /workspace/files.txt",
        report_name="workspace-summary",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()
    initial = workspace_batch_module.serialize_workspace_batch_run(db_session, workflow)

    unrelated_relative_path = "workspace-unrelated/report.txt"
    unrelated_path = workspace_batch_module.settings.fs_data_root / unrelated_relative_path
    unrelated_path.parent.mkdir(parents=True, exist_ok=True)
    unrelated_path.write_text("unrelated", encoding="utf-8")
    db_session.add(
        Artifact(
            case_id=None,
            workspace_id=workspace.id,
            kind=ArtifactKind.report,
            name="report.txt",
            relative_path=unrelated_relative_path,
            mime_type="text/plain",
            size_bytes=len("unrelated"),
            metadata_json={"run_id": "wf-other"},
        )
    )
    db_session.commit()

    refreshed = workspace_batch_module.serialize_workspace_batch_run(db_session, workflow)
    detail = workspace_batch_module.serialize_workspace_batch_detail(db_session, workflow)

    assert refreshed.artifact_count == initial.artifact_count
    assert all(artifact.metadata.get("run_id") == summary.run_id for artifact in detail.artifacts)


def test_prepare_workspace_command_inputs_writes_direct_case_bind_manifest(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_command_run", lambda run_id: "task-workspace-1")
    summary = workspace_batch_module.create_workspace_command_run(
        db_session,
        context,
        workspace,
        command="find /cases -maxdepth 2 -type f > /workspace/files.txt",
        report_name="workspace-summary",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()

    analysis_id = workspace_batch_module._analysis_id_from_run(workflow)
    analysis_dir, cases_dir = workspace_batch_fs_module.prepare_workspace_command_inputs(
        db_session,
        workflow,
        workspace,
        analysis_id=analysis_id,
    )

    expected_case_a_target = str(
        workspace_batch_module.case_storage_dir(workspace_batch_module.settings, workspace.id, case_a.id).resolve()
    )
    expected_case_b_target = str(
        workspace_batch_module.case_storage_dir(workspace_batch_module.settings, workspace.id, case_b.id).resolve()
    )

    assert not (cases_dir / "case-a").exists()
    assert not (cases_dir / "case-b").exists()

    manifest = json.loads((analysis_dir / "cases.json").read_text(encoding="utf-8"))
    assert [entry["mount_path"] for entry in manifest] == ["/cases/case-a", "/cases/case-b"]
    bind_manifest = json.loads((cases_dir / "cases.json").read_text(encoding="utf-8"))
    assert [entry["mount_path"] for entry in bind_manifest] == ["/cases/case-a", "/cases/case-b"]
    assert [entry["host_path"] for entry in bind_manifest] == [expected_case_a_target, expected_case_b_target]


def test_cancel_workspace_batch_run_marks_unfinished_runs_canceled(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    monkeypatch.setattr(
        workspace_batch_module,
        "queue_workspace_batch_case",
        lambda run_id, case_id, *, is_probe: f"task-{case_id}",
    )
    summary = workspace_batch_module.create_workspace_batch_run(
        db_session,
        context,
        workspace,
        command="mri_synthseg --help | head",
        report_name="synthseg-batch",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()

    canceled: list[str] = []
    from api_service.jobs import job_manager

    monkeypatch.setattr(job_manager, "cancel", lambda task_id: bool(canceled.append(task_id)) or True)

    detail = workspace_batch_module.cancel_workspace_batch_run(db_session, workflow)
    runs = db_session.query(Run).filter(Run.parent_run_id == summary.run_id).all()

    assert detail.status == "canceled"
    assert all(run.status.value == "canceled" for run in runs)
    assert canceled == [f"task-{case_a.id}"]


def test_cancel_workspace_command_run_revokes_single_task(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context

    monkeypatch.setattr(workspace_batch_module, "queue_workspace_command_run", lambda run_id: "task-workspace-1")
    summary = workspace_batch_module.create_workspace_command_run(
        db_session,
        context,
        workspace,
        command="find /cases -maxdepth 2 -type f > /workspace/files.txt",
        report_name="workspace-summary",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-1",
        provider_name="openai-compatible",
        model_name="qwen",
    )
    workflow = db_session.query(Run).filter(Run.id == summary.run_id).one()

    canceled: list[str] = []
    from api_service.jobs import job_manager

    monkeypatch.setattr(job_manager, "cancel", lambda task_id: bool(canceled.append(task_id)) or True)

    detail = workspace_batch_module.cancel_workspace_batch_run(db_session, workflow)

    assert detail.status == "canceled"
    assert canceled == ["task-workspace-1"]


def test_result_text_has_execution_error_detects_runtime_failures():
    assert workspace_batch_reports_module.result_text_has_execution_error("Error executing workspace_bash (Exit code 1).")
    assert workspace_batch_reports_module.result_text_has_execution_error("Error: workspace_bash timed out after 300s.")
    assert not workspace_batch_reports_module.result_text_has_execution_error("Successfully executed workspace_bash.\nSTDOUT:\nok")


def test_runtime_visible_data_path_maps_repo_data_root_to_host_path(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_batch_module.settings, "fs_data_root", tmp_path / "neurocade-data")
    target = workspace_batch_module.settings.fs_data_root / ".workspace-inputs" / "analysis-1" / "cases"
    target.mkdir(parents=True)

    mapped = workspace_batch_module._runtime_visible_data_path(target)

    assert mapped == str(target.resolve())
