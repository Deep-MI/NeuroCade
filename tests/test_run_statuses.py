"""Test shared run status helpers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend_common.db import RunStatus  # noqa: E402
from backend_common.run_statuses import (  # noqa: E402
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
)


def test_active_status_sets_only_include_current_in_progress_states():
    assert frozenset({RunStatus.queued, RunStatus.running}) == ACTIVE_RUN_STATUSES
    assert frozenset({
        RunStatus.completed,
        RunStatus.failed,
        RunStatus.canceled,
    }) == TERMINAL_RUN_STATUSES
