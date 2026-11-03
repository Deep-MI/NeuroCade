"""Shared output activity checks for workflows, imports, and case snapshots.

One writer at a time per case is enforced from two observable facts, never from
a stored verdict:

* the application's own schedule -- a queued or running row means work is
  expected, and it clears itself when the row reaches a terminal status;
* the container runtime -- a tool container that outlived its job is still
  visible to the daemon through its run-ID label, and stops being visible the
  moment it exits.

Neither can outlive the condition it describes, so no case can be left
permanently unavailable by a failure that happened once.
"""

import fcntl
import hashlib
import logging
from contextlib import contextmanager

from sqlalchemy import or_

from backend_common.db import PacsImport, Run
from backend_common.mcp_lifecycle import reserve_scope_deletion
from backend_common.run_statuses import run_is_active
from backend_common.settings import get_settings
from backend_common.submission_lock import submission_lock

logger = logging.getLogger(__name__)


class OutputBusy(ValueError):
    """A writer or snapshot reader is currently using the selected output scope."""


def _lock_path(workspace_id, case_id):
    root = get_settings().fs_data_root / ".locks" / "case-files"
    workspace = hashlib.sha256(workspace_id.encode()).hexdigest()
    scope = hashlib.sha256(case_id.encode()).hexdigest() if case_id is not None else "workspace"
    return root / f"{workspace}-{scope}.lock"


def _read_lock_paths(workspace_id, case_id):
    workspace = _lock_path(workspace_id, None)
    if case_id is not None:
        return [workspace, _lock_path(workspace_id, case_id)]
    return workspace.parent.glob(workspace.name.removesuffix("workspace.lock") + "*.lock")


def _live_writer_run_ids() -> set[str]:
    """Ask the runtime which runs still have a container writing.

    An unreachable or undetermined runtime yields an empty set. That is a
    deliberate narrowing: a bridge the application cannot reach also cannot
    start a competing writer, and the scheduled-run check above still applies.
    The alternative -- refusing on an unanswerable question -- is what made a
    single failed query able to block a case forever.
    """
    from neurocade_runtime_tools.bridge_client import BridgeClient

    try:
        determined, run_ids = BridgeClient.from_environment().active_writers()
    except Exception as exc:  # noqa: BLE001 - any bridge failure means "unknown"
        logger.debug("output_activity.writer_probe_unavailable error=%s", exc)
        return set()
    if not determined:
        logger.debug("output_activity.writer_probe_undetermined tracked=%d", len(run_ids))
    return run_ids


def ensure_outputs_idle(db, workspace_id, case_id=None, *, exclude_run_id=None):
    imports = db.query(PacsImport).filter(
        PacsImport.workspace_id == workspace_id,
        or_(PacsImport.state.in_(("queued", "running", "canceling")), PacsImport.error_code == "cleanup_failed"),
    )
    runs = db.query(Run).filter(Run.workspace_id == workspace_id)
    if case_id is not None:
        imports = imports.filter(PacsImport.case_id == case_id)
        runs = runs.filter(or_(Run.case_id == case_id, Run.case_id.is_(None)))
    if exclude_run_id is not None:
        runs = runs.filter(Run.id != exclude_run_id)
    if imports.first():
        raise OutputBusy("Case has an active PACS import or unresolved import cleanup")
    if any(run_is_active(run) for run in runs):
        raise OutputBusy("Another workflow is queued or running for this case")
    live_writers = _live_writer_run_ids()
    if live_writers and runs.filter(Run.id.in_(live_writers)).first():
        raise OutputBusy("A previous workflow's container is still writing this case's outputs")
    for path in _read_lock_paths(workspace_id, case_id):
        if not path.exists():
            continue
        # File locks also protect downloads from a separate admin process.
        # Never unlink lock files: that would allow locks on different inodes.
        with path.open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OutputBusy("Case files are in use by an upload or download; retry when it finishes") from exc


@contextmanager
def reserve_case_files(db, workspace_id, case_id):
    """Reserve one idle case for file I/O without holding global admission."""
    path = _lock_path(workspace_id, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        with submission_lock:
            # Serialize with admin deletions in other processes as well as with
            # local submissions. Release the database write reservation promptly.
            reserve_scope_deletion(db)
            ensure_outputs_idle(db, workspace_id, case_id)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OutputBusy("An upload or download of this case is already in progress") from exc
            db.commit()
        yield
