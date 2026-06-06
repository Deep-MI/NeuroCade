"""Provide shared backend run utilities for NeuroCade."""

from __future__ import annotations

from typing import Literal

from backend_common.db import AssistantScope, Run


WORKSPACE_BATCH_ACTION = "workspace_batch_bash"
WORKSPACE_COMMAND_ACTION = "workspace_bash"
WORKSPACE_RUN_ACTIONS = frozenset({WORKSPACE_BATCH_ACTION, WORKSPACE_COMMAND_ACTION})

WorkspaceExecutionMode = Literal["workspace_wide", "per_case"]


def _normalized_action(action: str | None) -> str:
    """Return the stripped action string, treating missing actions as empty."""
    return str(action or "").strip()


def is_workspace_run_action(action: str | None) -> bool:
    """Return whether an action executes commands in a workspace."""
    return _normalized_action(action) in WORKSPACE_RUN_ACTIONS


def is_workspace_wide_action(action: str | None) -> bool:
    """Return whether an action runs once for the whole workspace."""
    return _normalized_action(action) == WORKSPACE_COMMAND_ACTION


def workspace_execution_mode(action: str | None) -> WorkspaceExecutionMode:
    """Map a workspace action to its execution mode."""
    normalized = _normalized_action(action)
    if normalized not in WORKSPACE_RUN_ACTIONS:
        raise ValueError(f"Unsupported workspace run action: {normalized or '<empty>'}")
    if normalized == WORKSPACE_COMMAND_ACTION:
        return "workspace_wide"
    return "per_case"


def is_workspace_run(parent_run: Run) -> bool:
    """Return whether a workspace run is a workspace command run."""
    return parent_run.scope_type == AssistantScope.workspace and is_workspace_run_action(parent_run.run_type)


def is_assistant_run(parent_run: Run) -> bool:
    """Return whether a workspace run should use assistant orchestration."""
    return not is_workspace_run(parent_run)
