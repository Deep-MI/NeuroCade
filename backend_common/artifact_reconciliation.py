"""Keep artifact rows aligned with files currently present in storage."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend_common.db import Artifact, AuditEvent, CaseEvent, Run
from backend_common.run_statuses import ACTIVE_RUN_STATUSES
from backend_common.storage import resolve_artifact_path


def _exists(artifact: Artifact) -> bool:
    try:
        return resolve_artifact_path(artifact).is_file()
    except (FileNotFoundError, ValueError):
        return False


def existing_artifacts(artifacts: list[Artifact]) -> list[Artifact]:
    """Return rows whose files exist without changing database state."""
    return [artifact for artifact in artifacts if _exists(artifact)]


def reconcile_artifacts(db: Session, artifacts: list[Artifact]) -> list[Artifact]:
    """Return existing artifacts and remove stable rows whose files are gone."""
    case_ids = {artifact.case_id for artifact in artifacts if artifact.case_id}
    active_case_ids = {
        case_id
        for (case_id,) in db.query(Run.case_id)
        .filter(Run.case_id.in_(case_ids), Run.status.in_(ACTIVE_RUN_STATUSES))
        .distinct()
        .all()
        if case_id is not None
    } if case_ids else set()

    existing: list[Artifact] = []
    missing_ids: list[str] = []
    for artifact in artifacts:
        if _exists(artifact):
            existing.append(artifact)
        elif artifact.case_id not in active_case_ids:
            missing_ids.append(artifact.id)

    if missing_ids:
        db.query(AuditEvent).filter(AuditEvent.artifact_id.in_(missing_ids)).update(
            {AuditEvent.artifact_id: None}, synchronize_session=False
        )
        db.query(CaseEvent).filter(CaseEvent.artifact_id.in_(missing_ids)).update(
            {CaseEvent.artifact_id: None}, synchronize_session=False
        )
        db.query(Artifact).filter(Artifact.id.in_(missing_ids)).delete(synchronize_session=False)
        db.flush()
    return existing


def reconcile_all_artifacts(db: Session) -> None:
    """Remove every stable artifact row whose file is no longer present."""
    reconcile_artifacts(db, db.query(Artifact).all())
