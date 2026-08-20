"""Focused tests for structured assistant approval presentations."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant.approval_presentations import (  # noqa: E402
    config_delete_approval_presentation,
    config_upsert_approval_presentation,
    file_edit_approval_presentation,
    file_write_approval_presentation,
    run_cancel_approval_presentation,
    workflow_approval_presentation,
)
from api_service.runtime_tools.workflow_catalog import load_workflow_catalog, upsert_user_workflow  # noqa: E402

from backend_common.db import AssistantScope, Base, Run, RunStatus  # noqa: E402
from backend_common.settings import get_settings  # noqa: E402


def test_workflow_approval_presentation_comes_from_catalog_config():
    presentation = workflow_approval_presentation(
        {},
        {"tool_id": "fastsurfer_segmentation", "inputs": ["/case/mri/001.mgz"]},
        settings=get_settings(),
    )

    assert presentation is not None
    assert presentation.title == "FastSurfer — Segmentation"
    assert presentation.description == "Run FastSurfer segmentation without cortical surface reconstruction."
    assert [item.model_dump() for item in presentation.inputs] == [{
        "name": "t1",
        "description": "T1-weighted input volume.",
        "path": "/case/mri/001.mgz",
    }]
    assert presentation.execution.model_dump() == {"mode": "background", "gpu": True}
    assert any(output.name == "whole_brain_segmentation" for output in presentation.outputs)


def test_file_approval_presentations_are_structured_and_bounded():
    write = file_write_approval_presentation(
        {"scope": AssistantScope.case.value},
        {"path": "/case/notes.txt", "content": "a" * 5_000},
    )
    edit = file_edit_approval_presentation(
        {"scope": AssistantScope.workspace.value},
        {
            "path": "/workspace/notes.txt",
            "old_text": "before",
            "new_text": "after",
            "replace_all": True,
        },
    )

    assert write is not None
    assert write.kind == "action"
    assert write.confirm_label == "Write file"
    assert write.sections[0].rows[0].value == "/case/notes.txt"
    assert "additional character(s) omitted" in write.details[0].content
    assert edit is not None
    assert edit.confirm_label == "Apply edit"
    assert edit.sections[0].rows[2].value == "Every occurrence"


def test_config_approval_presentations_explain_create_and_delete_effect(tmp_path):
    settings = get_settings().model_copy(update={"fs_data_root": tmp_path})
    workflow = load_workflow_catalog().tools[0].model_copy(deep=True)
    workflow.id = "private_confirmation_test"
    workflow.ui.label = "Private confirmation test"
    definition = workflow.model_dump(mode="json", by_alias=True, exclude_none=True)
    state = {"context": SimpleNamespace(user=SimpleNamespace(id="approval-user"))}

    create = config_upsert_approval_presentation(state, {"definition": definition}, settings=settings)
    upsert_user_workflow(settings, "approval-user", definition)
    delete = config_delete_approval_presentation(state, {"tool_id": workflow.id}, settings=settings)

    assert create is not None
    assert create.title == "Save Private confirmation test?"
    assert create.sections[0].rows[0].value == "Create private workflow"
    assert create.confirm_label == "Save workflow"
    assert delete is not None
    assert delete.title == "Delete Private confirmation test?"
    assert delete.tone == "danger"
    assert delete.sections[0].rows[2].value == "No workflow with this ID"


def test_run_cancel_approval_identifies_exact_active_run():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db_session:
        run = Run(
            id="run-confirmation",
            scope_type="case",
            case_id="case-1",
            workspace_id="workspace-1",
            created_by_user_id="user-1",
            status=RunStatus.running,
            run_type="fastsurfer_segmentation",
            input_json={},
            result_json={},
        )
        db_session.add(run)
        db_session.commit()

        presentation = run_cancel_approval_presentation(
            {
                "db": db_session,
                "context": SimpleNamespace(user=SimpleNamespace(id="user-1")),
                "scope": "case",
                "workspace_id": "workspace-1",
                "case_id": "case-1",
            },
            {"run_id": run.id},
            settings=get_settings(),
        )

        assert presentation is not None
        assert presentation.kind == "action"
        assert presentation.confirm_label == "Cancel run"
        assert presentation.tone == "danger"
        assert presentation.sections[0].rows[0].value == run.id
