"""Provide shared backend concurrency utilities for NeuroCade.

These helpers add ``SELECT ... FOR UPDATE`` row locks on Postgres to serialize
read-modify-write sections. On SQLite (the single-node monolith default) there
are no row-level locks, but serialization is provided at the engine level
instead: every write transaction begins with ``BEGIN IMMEDIATE`` (see
``backend_common.db``), so concurrent writers are serialized whole-transaction
and these helpers safely degrade to a plain reload.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from backend_common.db import AssistantThread, Case, Workspace


def supports_row_level_locks(db: Session) -> bool:
    """Return whether the active database dialect supports row-level locks.

    SQLite serializes write transactions via ``BEGIN IMMEDIATE`` instead, so it
    intentionally returns ``False`` here without losing read-modify-write safety.
    """
    return db.get_bind().dialect.name == "postgresql"


def lock_case_for_update(db: Session, case: Case) -> Case:
    """Reload and lock a case row when row-level locks are available."""
    if not supports_row_level_locks(db):
        return case
    return db.query(Case).filter(Case.id == case.id).with_for_update().one()


def lock_cases_for_update(db: Session, case_ids: Sequence[str]) -> list[Case]:
    """Fetch cases in stable order and lock them when supported."""
    ordered_ids = sorted({case_id for case_id in case_ids if case_id})
    if not ordered_ids:
        return []

    query = db.query(Case).filter(Case.id.in_(ordered_ids)).order_by(Case.id.asc())
    if supports_row_level_locks(db):
        query = query.with_for_update()
    return query.all()


def lock_workspace_for_update(db: Session, workspace: Workspace) -> Workspace:
    """Reload and lock a workspace row when row-level locks are available."""
    if not supports_row_level_locks(db):
        return workspace
    return db.query(Workspace).filter(Workspace.id == workspace.id).with_for_update().one()


def lock_assistant_thread_for_update(db: Session, thread: AssistantThread) -> AssistantThread:
    """Reload and lock an assistant thread row when row-level locks are available."""
    if not supports_row_level_locks(db):
        return thread
    return db.query(AssistantThread).filter(AssistantThread.id == thread.id).with_for_update().one()
