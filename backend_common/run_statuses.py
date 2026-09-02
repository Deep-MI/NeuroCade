"""Provide shared backend run statuses utilities for NeuroCade."""

from backend_common.db import RunStatus

ACTIVE_RUN_STATUSES = frozenset({RunStatus.queued, RunStatus.running})
TERMINAL_RUN_STATUSES = frozenset({RunStatus.completed, RunStatus.failed, RunStatus.canceled})
