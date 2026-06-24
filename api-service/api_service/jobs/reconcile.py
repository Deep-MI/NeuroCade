"""Startup reconciliation of runs left active by a crash.

The in-process JobWorker holds queued/running jobs in memory, so a process
restart loses any in-flight work. Rows in the ``runs`` table that were left
``queued``/``running`` by the previous process can never be resumed and are
marked ``failed`` on startup so the UI does not show perpetually-active runs.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

from backend_common.db import Run, RunStatus

logger = logging.getLogger(__name__)

_ACTIVE = (RunStatus.queued, RunStatus.running)


def reconcile_interrupted_runs(session_factory: Callable[[], Session]) -> int:
    """Mark runs left active by a previous process as failed. Returns the count."""
    with session_factory() as db:
        stuck = db.query(Run).filter(Run.status.in_(_ACTIVE)).all()
        for run in stuck:
            run.status = RunStatus.failed
            if not run.error_message:
                run.error_message = "Interrupted by an application restart."
        if stuck:
            db.commit()
            logger.warning("jobs.reconcile marked %d interrupted run(s) as failed", len(stuck))
        return len(stuck)
