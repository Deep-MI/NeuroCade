"""Test shared run status helpers."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend_common.db import RunStatus  # noqa: E402
from backend_common.run_statuses import (  # noqa: E402
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    normalize_run_status,
)


def test_active_status_sets_only_include_current_in_progress_states():
    assert ACTIVE_RUN_STATUSES == frozenset({RunStatus.queued, RunStatus.running})
    assert TERMINAL_RUN_STATUSES == frozenset({
        RunStatus.completed,
        RunStatus.failed,
        RunStatus.canceled,
    })


def test_normalize_run_status_maps_supported_runtime_statuses():
    assert normalize_run_status("starting") == RunStatus.running
    assert normalize_run_status("finished") == RunStatus.completed
    assert normalize_run_status("error") == RunStatus.failed
    assert normalize_run_status(" queued ") == RunStatus.queued
    assert normalize_run_status("not-a-status") is None
