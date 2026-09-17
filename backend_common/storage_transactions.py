"""Recoverable filesystem staging for database-coordinated deletions."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class StagedStorage:
    """A path moved aside until its corresponding database commit succeeds."""

    original_path: Path
    staged_path: Path


def stage_path_for_deletion(path: Path, trash_root: Path) -> StagedStorage | None:
    """Atomically move an existing path into an application-local trash area."""
    if not path.exists() and not path.is_symlink():
        return None
    trash_root.mkdir(parents=True, exist_ok=True)
    staged_path = trash_root / uuid4().hex
    path.replace(staged_path)
    return StagedStorage(original_path=path, staged_path=staged_path)


def restore_staged_path(staged: StagedStorage | None) -> None:
    """Restore a staged path after a failed database transaction."""
    if staged is None or (not staged.staged_path.exists() and not staged.staged_path.is_symlink()):
        return
    if staged.original_path.exists() or staged.original_path.is_symlink():
        raise FileExistsError(f"Cannot restore staged storage over {staged.original_path}")
    staged.original_path.parent.mkdir(parents=True, exist_ok=True)
    staged.staged_path.replace(staged.original_path)


def finalize_staged_path(staged: StagedStorage | None) -> None:
    """Permanently remove a staged path after its database transaction commits."""
    if staged is None:
        return
    path = staged.staged_path
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def stage_deletion_for_transaction(db, path: Path, trash_root: Path) -> None:
    """Keep admin-reset storage recoverable until the outer DB transaction ends."""
    from sqlalchemy import event

    staged = stage_path_for_deletion(path, trash_root)
    if staged is None:
        return
    db.info.setdefault("staged_deletions", []).append(staged)
    if db.info.get("staged_deletion_hooks"):
        return
    db.info["staged_deletion_hooks"] = True

    def committed(session):
        if session.in_nested_transaction():
            return
        for item in session.info.pop("staged_deletions", []):
            finalize_staged_path(item)

    def restore(session):
        for item in reversed(session.info.pop("staged_deletions", [])):
            if item.original_path.exists():
                # A reset may already have created its replacement sample. Preserve
                # that new tree separately while putting the original back.
                stage_path_for_deletion(item.original_path, item.staged_path.parent / "rollback-conflicts")
            restore_staged_path(item)

    def ended(session, transaction):
        if transaction.parent is None:
            restore(session)

    event.listen(db, "after_commit", committed)
    event.listen(db, "after_rollback", restore)
    event.listen(db, "after_transaction_end", ended)
