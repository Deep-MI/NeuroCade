"""Reconcile application run rows with durable background-job recovery."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend_common.db import Run, RunStatus
from backend_common.run_statuses import ACTIVE_RUN_STATUSES

logger = logging.getLogger(__name__)

def reconcile_interrupted_runs(
    session_factory: Callable[[], Session],
    *,
    recovered_job_ids: set[str] | None = None,
) -> int:
    """Fail active runs without a recovered queued job and return the count."""
    recovered = recovered_job_ids or set()
    with session_factory() as db:
        active_runs = db.query(Run).filter(Run.status.in_(ACTIVE_RUN_STATUSES)).all()
        stuck = [
            run
            for run in active_runs
            if run.job_id not in recovered
        ]
        for run in stuck:
            run.status = RunStatus.failed
            if not run.error_message:
                run.error_message = "Interrupted by an application restart."
        if stuck:
            db.commit()
            logger.warning("jobs.reconcile marked %d interrupted run(s) as failed", len(stuck))
        return len(stuck)
