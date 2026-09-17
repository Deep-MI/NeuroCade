"""Scope deletion invalidates external work without broadening its authority."""

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend_common.db import AssistantToolExecution, McpClient, is_sqlite_lock_error


def tombstone_case_invocations(db: Session, case_id: str) -> None:
    # Retain the client/key digest so recreating a canonical case ID cannot replay
    # an old mutation. These denied records are diagnostics, never workspace work.
    db.query(AssistantToolExecution).filter_by(source="mcp", case_id=case_id).update(
        {
            "case_id": None,
            "status": "denied",
            "arguments_json": {},
            "approval_json": {},
            "result_json": {
                "content": "Case was deleted; invocation is permanently invalidated.",
                "is_error": True,
                "details": {"scope_deleted": True, "deleted_case_id": case_id},
            },
            "error_message": "Case was deleted",
            "expires_at": None,
        },
        synchronize_session=False,
    )


def purge_workspace_clients(db: Session, workspace_id: str) -> None:
    # Workspace deletion ends pairing and its idempotency namespace together.
    db.query(AssistantToolExecution).filter_by(workspace_id=workspace_id, source="mcp").delete(synchronize_session=False)
    db.query(McpClient).filter_by(workspace_id=workspace_id).delete(synchronize_session=False)


class ScopeDeletionConflict(RuntimeError):
    """A stale transaction must be retried before any storage is moved."""


def reserve_scope_deletion(db: Session) -> None:
    # Preserve pending caller work. SQLite refuses a stale-snapshot upgrade;
    # that failure happens before staging, rather than resetting the session.
    try:
        db.flush()
        db.execute(text("UPDATE runs SET id=id WHERE 0"))
    except OperationalError as exc:
        if is_sqlite_lock_error(exc):
            raise ScopeDeletionConflict("Workspace activity changed; retry deletion") from exc
        raise
    db.expire_all()
