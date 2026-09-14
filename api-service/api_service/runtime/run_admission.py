"""Serialize admission to overlapping output roots in the single-process runtime."""

from functools import wraps

from sqlalchemy.orm import Session, object_session

from backend_common.output_activity import OutputBusy, ensure_outputs_idle
from backend_common.submission_lock import serialize_submission as serialize_submission
from backend_common.submission_lock import submission_lock as _submission_lock

__all__ = ["guard_output_submission", "serialize_submission"]


def guard_output_submission(function):
    @wraps(function)
    def guarded(run, workflow, *args, **kwargs):
        with _submission_lock:
            owner = object_session(run)
            if owner is None:
                raise ValueError("Run must belong to the submitting database session")
            with Session(bind=owner.get_bind()) as db:
                try:
                    ensure_outputs_idle(db, run.workspace_id, run.case_id, exclude_run_id=run.id)
                except OutputBusy as exc:
                    raise ValueError(f"OUTPUT_BUSY: {exc}") from exc
            return function(run, workflow, *args, **kwargs)

    return guarded
