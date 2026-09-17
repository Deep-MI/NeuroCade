"""Shared output ownership checks for workflows, imports, and case snapshots."""

import fcntl
import hashlib
from contextlib import contextmanager

from sqlalchemy import or_

from backend_common.db import PacsImport, Run
from backend_common.mcp_lifecycle import reserve_scope_deletion
from backend_common.run_statuses import run_owns_outputs
from backend_common.settings import get_settings
from backend_common.submission_lock import submission_lock


class OutputBusy(ValueError):
    """A writer or snapshot reader still owns the selected output scope."""


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
    if any(run_owns_outputs(run) for run in runs):
        raise OutputBusy("Processing may still own this case or workspace's outputs")
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
