"""Provide shared backend run statuses utilities for NeuroCade."""

from backend_common.db import RunStatus

ACTIVE_RUN_STATUSES = frozenset({RunStatus.queued, RunStatus.running})
TERMINAL_RUN_STATUSES = frozenset({RunStatus.completed, RunStatus.failed, RunStatus.canceled})


def run_is_active(run) -> bool:
    """Return whether the application still expects this run to do work.

    Whether a *container* is still writing is a separate question, answered by
    the runtime rather than by a stored field: see
    ``backend_common.output_activity``.
    """
    return run.status in ACTIVE_RUN_STATUSES
