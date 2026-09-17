"""Provide shared backend run statuses utilities for NeuroCade."""

from backend_common.db import RunStatus

ACTIVE_RUN_STATUSES = frozenset({RunStatus.queued, RunStatus.running})
TERMINAL_RUN_STATUSES = frozenset({RunStatus.completed, RunStatus.failed, RunStatus.canceled})


def run_owns_outputs(run) -> bool:
    """Terminal display state does not prove an interrupted writer has stopped."""
    return run.status in ACTIVE_RUN_STATUSES or (run.result_json or {}).get("output_ownership") in {"held", "unresolved"}
