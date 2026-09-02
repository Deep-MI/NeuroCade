"""Provide shared backend admin reset utilities for NeuroCade."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend_common.case_storage import delete_case_storage, workspace_storage_dir
from backend_common.db import (
    Artifact,
    AssistantMessage,
    AssistantThread,
    AssistantTurn,
    AuditEvent,
    Case,
    CaseEvent,
    Run,
    User,
    Workspace,
    WorkspaceMembership,
)
from backend_common.sample_seed import ensure_sample_case, sample_case_id_for_workspace
from backend_common.workspace_bootstrap import ensure_personal_workspace


@dataclass
class ResetCounts:
    workspaces_deleted: int = 0
    cases_deleted: int = 0


def _remove_workspace_storage_root(settings, workspace: Workspace) -> None:
    """Delete the on-disk storage tree for a workspace if it exists."""
    workspace_root = workspace_storage_dir(settings, workspace.id)
    if workspace_root.exists():
        shutil.rmtree(workspace_root)


def _assistant_thread_ids_for_case(db: Session, case_id: str) -> list[str]:
    """Return assistant thread IDs associated with a case."""
    return [
        thread_id
        for (thread_id,) in db.query(AssistantThread.id)
        .filter(AssistantThread.case_id == case_id)
        .all()
    ]


def purge_case(db: Session, settings, case: Case, workspace: Workspace | None) -> None:
    """Delete a case and its storage, artifacts, runs, and assistant data."""
    if workspace is not None:
        delete_case_storage(settings, case, workspace)

    artifact_ids = [
        artifact_id
        for (artifact_id,) in db.query(Artifact.id)
        .filter(Artifact.case_id == case.id)
        .all()
    ]
    assistant_thread_ids = _assistant_thread_ids_for_case(db, case.id)
    if assistant_thread_ids:
        db.query(AssistantMessage).filter(AssistantMessage.thread_id.in_(assistant_thread_ids)).delete(synchronize_session=False)
        db.query(AssistantTurn).filter(AssistantTurn.thread_id.in_(assistant_thread_ids)).delete(synchronize_session=False)

    if artifact_ids:
        db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
        db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    db.flush()
    db.query(AssistantMessage).filter(AssistantMessage.case_id == case.id).delete(synchronize_session=False)
    db.query(AssistantTurn).filter(AssistantTurn.case_id == case.id).delete(synchronize_session=False)
    db.query(CaseEvent).filter(CaseEvent.case_id == case.id).delete(synchronize_session=False)
    db.query(Artifact).filter(Artifact.case_id == case.id).delete(synchronize_session=False)
    db.query(Run).filter(Run.case_id == case.id).delete(synchronize_session=False)
    db.query(AssistantThread).filter(AssistantThread.case_id == case.id).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.case_id == case.id).delete(synchronize_session=False)
    db.delete(case)
    db.flush()


def purge_workspace(db: Session, settings, workspace: Workspace) -> ResetCounts:
    """Delete a workspace and all database and storage records owned by it."""
    counts = ResetCounts()
    cases = (
        db.query(Case)
        .filter(Case.workspace_id == workspace.id)
        .order_by(Case.created_at.asc(), Case.id.asc())
        .all()
    )
    for case in cases:
        purge_case(db, settings, case, workspace)
        counts.cases_deleted += 1

    workspace_artifact_ids = [
        artifact_id
        for (artifact_id,) in db.query(Artifact.id)
        .filter(Artifact.workspace_id == workspace.id)
        .all()
    ]
    if workspace_artifact_ids:
        db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(workspace_artifact_ids)).delete(synchronize_session=False)
        db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(workspace_artifact_ids)).delete(synchronize_session=False)
    db.query(AssistantMessage).filter(AssistantMessage.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(AssistantTurn).filter(AssistantTurn.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(CaseEvent).filter(CaseEvent.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(Artifact).filter(Artifact.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(Run).filter(Run.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(AssistantThread).filter(AssistantThread.workspace_id == workspace.id).delete(synchronize_session=False)
    db.query(WorkspaceMembership).filter(WorkspaceMembership.workspace_id == workspace.id).delete(synchronize_session=False)
    _remove_workspace_storage_root(settings, workspace)
    db.delete(workspace)
    db.flush()

    counts.workspaces_deleted += 1
    return counts


def reset_owned_workspaces(db: Session, settings, user_ids: list[str] | None = None) -> ResetCounts:
    """Delete owned workspaces, optionally limited to the given owner user IDs."""
    query = db.query(Workspace).order_by(Workspace.created_at.asc(), Workspace.id.asc())
    if user_ids:
        query = query.filter(Workspace.owner_user_id.in_(user_ids))

    counts = ResetCounts()
    workspaces = query.all()
    for workspace in workspaces:
        workspace_counts = purge_workspace(db, settings, workspace)
        counts.workspaces_deleted += workspace_counts.workspaces_deleted
        counts.cases_deleted += workspace_counts.cases_deleted
    return counts


def reset_sample_case_for_user(db: Session, settings, user: User) -> bool:
    """Recreate a user's sample case after removing any existing sample case."""
    workspace = ensure_personal_workspace(db, settings, user)

    case_id = sample_case_id_for_workspace(workspace.id)
    canonical_case = db.get(Case, case_id)
    if canonical_case is not None:
        purge_case(db, settings, canonical_case, workspace)

    seeded_case = ensure_sample_case(db, user)
    db.flush()
    return seeded_case is not None
