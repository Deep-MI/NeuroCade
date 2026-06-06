"""Provide shared backend run statuses utilities for NeuroCade."""

from __future__ import annotations

from backend_common.db import RunStatus


ACTIVE_RUN_STATUSES = frozenset({RunStatus.queued, RunStatus.running})
TERMINAL_RUN_STATUSES = frozenset({RunStatus.completed, RunStatus.failed, RunStatus.canceled})


def normalize_run_status(status: str | None) -> RunStatus | None:
    """Map an external run status string to a known backend run status."""
    if status is None:
        return None

    normalized = status.strip().lower()
    mapping = {
        "queued": RunStatus.queued,
        "starting": RunStatus.running,
        "running": RunStatus.running,
        "finished": RunStatus.completed,
        "completed": RunStatus.completed,
        "error": RunStatus.failed,
        "failed": RunStatus.failed,
        "canceled": RunStatus.canceled,
    }
    return mapping.get(normalized)
