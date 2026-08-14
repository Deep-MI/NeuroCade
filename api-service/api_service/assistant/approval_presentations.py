"""Deterministic user-facing descriptions for assistant approvals."""

from __future__ import annotations

import json
from typing import Any

from api_service.runtime_tools.workflow_catalog import resolve_workflow
from backend_common.settings import get_settings

settings = get_settings()


def _user_id(state: dict[str, Any]) -> str | None:
    context = state.get("context")
    user = getattr(context, "user", None)
    value = getattr(user, "id", None)
    return str(value) if value else None


def workflow_approval_presentation(
    state: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
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

    presented_inputs = []
    for index, path in enumerate(inputs):
        configured = workflow.inputs[index] if index < len(workflow.inputs) else None
        presented_inputs.append(
            {
                "name": configured.name if configured is not None else f"input_{index + 1}",
                "description": configured.description if configured is not None else "Workflow input",
                "path": str(path),
            }
        )
    return {
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


def build_approval_presentation(
    state: dict[str, Any],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    if name == "tool_call":
        return workflow_approval_presentation(state, arguments)
    return None


def approval_description(
    name: str,
    arguments: dict[str, Any],
    presentation: dict[str, Any] | None = None,
) -> str:
    """Return a concise fallback sentence for chat history and simple clients."""
    if presentation is not None:
        return f"run {presentation['title']}"
    path = arguments.get("path")
    if name == "write" and isinstance(path, str):
        return f"write `{path}`"
    if name == "edit" and isinstance(path, str):
        return f"edit `{path}`"
    argument_summary = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    return f"run `{name}` with {argument_summary[:500]}"
