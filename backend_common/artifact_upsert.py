"""Provide shared backend artifact upsert utilities for NeuroCade."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend_common.db import Artifact


def insert_artifact_if_missing(db: Session, values: dict, *, case_scoped: bool) -> Artifact | None:
    """Insert an artifact row, or return the existing artifact for the same scope and path."""
    artifact_values = dict(values)
    artifact_values.setdefault("id", str(uuid4()))

    if case_scoped:
        conflict_columns = ["case_id", "relative_path"]
        conflict_where = text("case_id IS NOT NULL")
        lookup_filters = (
            Artifact.case_id == artifact_values.get("case_id"),
            Artifact.relative_path == artifact_values.get("relative_path"),
        )
    else:
        conflict_columns = ["workspace_id", "relative_path"]
        conflict_where = text("case_id IS NULL AND workspace_id IS NOT NULL")
        lookup_filters = (
            Artifact.workspace_id == artifact_values.get("workspace_id"),
            Artifact.case_id.is_(None),
            Artifact.relative_path == artifact_values.get("relative_path"),
        )

    statement = (
        sqlite_insert(Artifact)
        .values(**artifact_values)
        .on_conflict_do_nothing(index_elements=conflict_columns, index_where=conflict_where)
    )
    result = db.execute(statement)
    if result.rowcount:
        return db.get(Artifact, artifact_values["id"])
    return db.query(Artifact).filter(*lookup_filters).order_by(Artifact.created_at.desc()).first()
