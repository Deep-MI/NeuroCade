"""Workflow failure codes survive persistence and API serialization."""
from typing import cast

import pytest
from api_service.cases.serializers import serialize_run_summary
from api_service.runtime.workflow_runs import mark_workflow_run_failed
from api_service.runtime_tools.errors import INPUT_ERRORS, WorkflowInputError, workflow_error_code
from test_pacs import context  # noqa: F401

from backend_common.db import Run, RunStatus


@pytest.mark.parametrize("code", INPUT_ERRORS)
def test_input_errors_have_stable_codes_and_legacy_mapping(code):
    error = WorkflowInputError(code)
    assert isinstance(error, ValueError)
    assert workflow_error_code(error) == code
    assert workflow_error_code(str(error)) == code
    assert workflow_error_code("unrelated " + str(error)) is None


def test_submission_failure_persists_code_and_serializes(context):  # noqa: F811
    db, _, _ = context
    run = Run(id="run", case_id="case", workspace_id="workspace", created_by_user_id="user", run_type="tool")
    db.add(run)
    db.commit()
    mark_workflow_run_failed(db, "run", "tool", WorkflowInputError("pacs_sequence_incompatible"))
    assert run.status == RunStatus.failed
    assert run.result_json["error_code"] == "pacs_sequence_incompatible"
    assert serialize_run_summary(run).error_code == "pacs_sequence_incompatible"


@pytest.mark.asyncio
async def test_preflight_returns_structured_422_and_keeps_failed_run(context, monkeypatch, tmp_path):  # noqa: F811
    from types import SimpleNamespace

    from api_service.cases import run_operations
    from api_service.schemas import StartRunRequest
    from fastapi import HTTPException

    from backend_common.db import Case, RoleEnum, Workspace

    db, auth, _ = context
    tool = SimpleNamespace(id="tool", label="Tool", inputs=[], outputs=[], neurodesk_image="test", execution=SimpleNamespace(gpu=False))
    monkeypatch.setattr(run_operations, "require_mutations_enabled", lambda: None)
    monkeypatch.setattr(run_operations, "resolve_workflow", lambda *_a, **_k: tool)
    monkeypatch.setattr(run_operations, "resolve_gpu_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(run_operations, "runtime_image_spec", lambda _: None)
    monkeypatch.setattr(run_operations, "get_case_for_user", lambda *_: (db.get(Case, "case"), db.get(Workspace, "workspace"), RoleEnum.user, tmp_path))
    monkeypatch.setattr(run_operations.workflow_runs, "workflow_run_snapshot", lambda *_a, **_k: {})

    def fail(*_args, **_kwargs):
        raise WorkflowInputError("pacs_sequence_incompatible")

    monkeypatch.setattr(run_operations, "prepare_workflow", fail)
    with pytest.raises(HTTPException) as error:
        await run_operations.start_neuroimaging_run(db, auth, request=StartRunRequest(tool_id="tool", case_id="case", input_artifact_ids=[]))
    assert error.value.status_code == 422
    detail = cast(dict[str, str], error.value.detail)
    assert detail["code"] == "pacs_sequence_incompatible"
    run = db.query(Run).one()
    assert run.status == RunStatus.failed
    assert serialize_run_summary(run).error_code == "pacs_sequence_incompatible"
    run.result_json = {}  # Older saved runs keep their corrective guidance.
    assert serialize_run_summary(run).error_code == "pacs_sequence_incompatible"
    run.error_message = "Unrelated failure"
    assert serialize_run_summary(run).error_code is None


def test_background_failure_persists_code(context, monkeypatch, tmp_path):  # noqa: F811
    from api_service.runtime import neuroimaging_tasks

    db, _, factory = context
    run = Run(id="background", case_id="case", workspace_id="workspace", created_by_user_id="user", run_type="tool")
    db.add(run)
    db.commit()
    monkeypatch.setattr(neuroimaging_tasks, "SessionLocal", factory)
    monkeypatch.setattr(neuroimaging_tasks, "_reconcile_case_artifacts", lambda _: None)

    def fail(*_args, **_kwargs):
        raise WorkflowInputError("pacs_sequence_incompatible")

    monkeypatch.setattr(neuroimaging_tasks, "execute_workflow", fail)
    result = neuroimaging_tasks.run_neuroimaging_workflow_task(run_id=run.id, tool_id="tool", inputs=[],
        bind_host_path=str(tmp_path), bind_container_path="/case", case_id="case", gpu_enabled=False)
    assert result["error_code"] == "pacs_sequence_incompatible"
    db.expire_all()
    assert run.result_json["error_code"] == "pacs_sequence_incompatible"
    assert serialize_run_summary(run).error_code == "pacs_sequence_incompatible"
