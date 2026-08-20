"""Deterministic user-facing descriptions for assistant approvals."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from api_service.assistant.approval_contracts import (
    AssistantActionApprovalPresentation,
    AssistantApprovalAction,
    AssistantApprovalTone,
    AssistantWorkflowApprovalPresentation,
)
from api_service.runtime_tools.workflow_catalog import (
    NeuroimagingWorkflow,
    load_user_workflow_catalog,
    load_workflow_catalog,
    resolve_workflow,
    workflow_source,
)
from backend_common.db import AssistantScope, Run
from backend_common.settings import Settings

PREVIEW_LIMIT = 4_000


def _preview(value: Any) -> str:
    text = str(value or "")
    if len(text) <= PREVIEW_LIMIT:
        return text
    omitted = len(text) - PREVIEW_LIMIT
    return f"{text[:PREVIEW_LIMIT]}\n\n… {omitted} additional character(s) omitted …"


def _row(label: str, value: Any, *, code: bool = False) -> dict[str, Any]:
    return {"label": label, "value": str(value), "code": code}


def _action_presentation(
    *,
    action: AssistantApprovalAction,
    title: str,
    description: str,
    confirm_label: str,
    sections: list[dict[str, Any]],
    details: list[dict[str, Any]] | None = None,
    tone: AssistantApprovalTone = "warning",
) -> AssistantActionApprovalPresentation:
    return AssistantActionApprovalPresentation.model_validate(
        {
            "kind": "action",
            "action": action,
            "title": title,
            "description": description,
            "confirm_label": confirm_label,
            "tone": tone,
            "sections": sections,
            "details": details or [],
        }
    )


def _user_id(state: dict[str, Any]) -> str | None:
    context = state.get("context")
    user = getattr(context, "user", None)
    value = getattr(user, "id", None)
    return str(value) if value else None


def workflow_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
    *,
    settings: Settings,
) -> AssistantWorkflowApprovalPresentation | None:
    """Resolve configured workflow metadata for one exact tool_call request."""
    tool_id = arguments.get("tool_id")
    inputs = arguments.get("inputs")
    if not isinstance(tool_id, str) or not isinstance(inputs, list):
        return None
    try:
        workflow = resolve_workflow(
            tool_id,
            settings=settings,
            user_id=_user_id(state),
        )
    except (OSError, ValueError):
        return None

    presented_inputs: list[dict[str, str]] = []
    for index, path in enumerate(inputs):
        configured = workflow.inputs[index] if index < len(workflow.inputs) else None
        presented_inputs.append(
            {
                "name": configured.name if configured is not None else f"input_{index + 1}",
                "description": configured.description if configured is not None else "Workflow input",
                "path": str(path),
            }
        )
    return AssistantWorkflowApprovalPresentation.model_validate(
        {
            "kind": "workflow",
            "title": workflow.label,
            "description": workflow.description,
            "details": workflow.details.strip(),
            "inputs": presented_inputs,
            "outputs": [
                {
                    "name": output.name,
                    "description": output.description,
                    "path": output.path,
                }
                for output in workflow.outputs
            ],
            "execution": {
                "mode": workflow.execution.mode,
                "gpu": workflow.execution.gpu,
            },
        }
    )


def config_upsert_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
    *,
    settings: Settings,
) -> AssistantActionApprovalPresentation | None:
    definition = arguments.get("definition")
    if not isinstance(definition, dict):
        return None
    try:
        workflow = NeuroimagingWorkflow.model_validate(definition)
    except ValueError:
        return None

    user_id = _user_id(state)
    existing_source: str | None = None
    if user_id is not None:
        try:
            resolve_workflow(workflow.id, settings=settings, user_id=user_id)
            existing_source = workflow_source(workflow.id, settings=settings, user_id=user_id)
        except (OSError, ValueError):
            pass
    operation = {
        None: "Create private workflow",
        "built_in": "Override built-in workflow",
        "user": "Replace private workflow",
        "user_override": "Update private override",
    }[existing_source]
    scheduling = "Background" if workflow.execution.mode == "background" else "Synchronous"
    compute = "GPU enabled" if workflow.execution.gpu else "CPU only"
    output_rows = [
        _row(output.name, f"{output.description} — {output.path}", code=True)
        for output in workflow.outputs
    ]
    sections: list[dict[str, Any]] = [
        {
            "label": "Catalog change",
            "rows": [
                _row("Operation", operation),
                _row("Workflow ID", workflow.id, code=True),
                _row("Image", workflow.image, code=True),
            ],
        },
        {
            "label": "Execution",
            "rows": [
                _row("Scheduling", f"{scheduling} · {compute}"),
                _row("Inputs", len(workflow.inputs)),
                _row("Outputs", len(workflow.outputs)),
            ],
        },
    ]
    if output_rows:
        sections.append({"label": "Declared outputs", "rows": output_rows})
    return _action_presentation(
        action="config_upsert",
        title=f"Save {workflow.label}?",
        description=workflow.description,
        confirm_label="Save workflow",
        sections=sections,
        details=[
            {"summary": "Workflow details", "content": workflow.details.strip()},
            {"summary": "Workflow script", "content": _preview(workflow.script), "language": "bash"},
        ],
    )


def config_delete_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
    *,
    settings: Settings,
) -> AssistantActionApprovalPresentation | None:
    tool_id = arguments.get("tool_id")
    user_id = _user_id(state)
    if not isinstance(tool_id, str) or user_id is None:
        return None
    try:
        overlay = load_user_workflow_catalog(settings, user_id)
        workflow = next(tool for tool in overlay.tools if tool.id == tool_id)
        built_in_ids = {tool.id for tool in load_workflow_catalog().tools}
    except (OSError, StopIteration, ValueError):
        return None
    reveals_builtin = tool_id in built_in_ids
    effect = (
        "Remove the private override and restore the built-in workflow."
        if reveals_builtin
        else "Remove this private workflow from the catalog."
    )
    return _action_presentation(
        action="config_delete",
        title=f"Delete {workflow.label}?",
        description=effect,
        confirm_label="Delete workflow",
        tone="danger",
        sections=[
            {
                "label": "Catalog change",
                "rows": [
                    _row("Workflow ID", workflow.id, code=True),
                    _row("Current source", "Private override" if reveals_builtin else "Private workflow"),
                    _row("After deletion", "Built-in workflow restored" if reveals_builtin else "No workflow with this ID"),
                ],
            }
        ],
        details=[
            {"summary": "Workflow details", "content": workflow.details.strip()},
            {"summary": "Workflow script", "content": _preview(workflow.script), "language": "bash"},
        ],
    )


def run_cancel_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
    *,
    settings: Settings,
) -> AssistantActionApprovalPresentation | None:
    run_id = arguments.get("run_id")
    db = state.get("db")
    workspace_id = state.get("workspace_id")
    if not isinstance(run_id, str) or not isinstance(db, Session) or not workspace_id:
        return None
    run = db.get(Run, run_id)
    expected_case_id = state.get("case_id") if state.get("scope") == AssistantScope.case.value else None
    if run is None or run.workspace_id != workspace_id or (expected_case_id and run.case_id != expected_case_id):
        return None
    try:
        workflow = resolve_workflow(run.run_type, settings=settings, user_id=_user_id(state))
        label = workflow.label
    except (OSError, ValueError):
        label = run.run_type
    return _action_presentation(
        action="run_cancel",
        title=f"Cancel {label}?",
        description="Stop this queued or running workflow. Outputs already written may remain in the case.",
        confirm_label="Cancel run",
        tone="danger",
        sections=[
            {
                "label": "Workflow run",
                "rows": [
                    _row("Run ID", run.id, code=True),
                    _row("Workflow", label),
                    _row("Current status", run.status.value),
                    _row("Scope", "Case" if run.case_id else "Workspace"),
                ],
            }
        ],
    )


def file_write_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
) -> AssistantActionApprovalPresentation | None:
    path = arguments.get("path")
    content = arguments.get("content")
    if not isinstance(path, str) or not isinstance(content, str):
        return None
    return _action_presentation(
        action="file_write",
        title=f"Write {path}?",
        description="Write the supplied UTF-8 content inside the active NeuroCade data scope.",
        confirm_label="Write file",
        sections=[
            {
                "label": "File change",
                "rows": [
                    _row("Path", path, code=True),
                    _row("Scope", "Case" if state.get("scope") == AssistantScope.case.value else "Workspace"),
                    _row("Content size", f"{len(content.encode('utf-8'))} bytes"),
                ],
            }
        ],
        details=[{"summary": "Content preview", "content": _preview(content), "language": "text"}],
    )


def file_edit_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
) -> AssistantActionApprovalPresentation | None:
    path = arguments.get("path")
    old_text = arguments.get("old_text")
    new_text = arguments.get("new_text")
    if not isinstance(path, str) or not isinstance(old_text, str) or not isinstance(new_text, str):
        return None
    replace_all = bool(arguments.get("replace_all", False))
    return _action_presentation(
        action="file_edit",
        title=f"Edit {path}?",
        description="Replace exact text inside this UTF-8 file.",
        confirm_label="Apply edit",
        sections=[
            {
                "label": "File change",
                "rows": [
                    _row("Path", path, code=True),
                    _row("Scope", "Case" if state.get("scope") == AssistantScope.case.value else "Workspace"),
                    _row("Replacement", "Every occurrence" if replace_all else "First occurrence only"),
                ],
            }
        ],
        details=[
            {"summary": "Text to replace", "content": _preview(old_text), "language": "text"},
            {"summary": "Replacement text", "content": _preview(new_text), "language": "text"},
        ],
    )


def approval_description(
    name: str,
    arguments: dict[str, Any],
    presentation: Mapping[str, Any] | None = None,
) -> str:
    """Return a concise fallback sentence for chat history and simple clients."""
    if presentation is not None and presentation.get("kind") == "workflow":
        return f"run {presentation['title']}"
    if presentation is not None and presentation.get("kind") == "action":
        return str(presentation["title"]).removesuffix("?").lower()
    path = arguments.get("path")
    if name == "write" and isinstance(path, str):
        return f"write `{path}`"
    if name == "edit" and isinstance(path, str):
        return f"edit `{path}`"
    argument_summary = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    return f"run `{name}` with {argument_summary[:500]}"
