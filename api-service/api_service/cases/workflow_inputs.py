"""Resolve case-relative workflow inputs without artifact rows."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from api_service.cases.files import resolve_case_entry
from api_service.runtime_tools.errors import WorkflowInputError
from api_service.runtime_tools.workflow_validation import validate_workflow_input
from api_service.schemas import WorkflowInputFile
from backend_common.db import Artifact, Case


@dataclass(frozen=True)
class ResolvedWorkflowInputs:
    container_paths: list[str]
    linked_source_id: str | None = None
    linked_source_relative: str = "."


def _validate_artifact_role(db: Session, case_id: str, relative_path: str, requirement: Any) -> None:
    if not requirement.volume_roles:
        return
    artifact = db.query(Artifact).filter_by(case_id=case_id, relative_path=relative_path).one_or_none()
    role = (artifact.metadata_json or {}).get("volume_role") if artifact else None
    if role and role not in requirement.volume_roles:
        raise WorkflowInputError("artifact_role_incompatible")


def resolve_case_workflow_inputs(
    db: Session,
    case: Case,
    case_dir: Path,
    input_files: list[WorkflowInputFile],
    requirements: list[Any],
) -> ResolvedWorkflowInputs:
    paths: list[str] = []
    linked_source_id: str | None = None
    linked_source_relative = "."
    for item, requirement in zip(input_files, requirements, strict=True):
        resolved = resolve_case_entry(
            db, case, case_dir, item.source, item.path, expected_kind=requirement.kind
        )
        _validate_artifact_role(db, case.id, resolved.relative_path, requirement)
        if resolved.managed_path is not None:
            validate_workflow_input(requirement, resolved.managed_path, db=db)
        root = "/case" if item.source == "managed" else "/linked-input"
        paths.append(root if item.path == "." else f"{root}/{item.path}")
        if resolved.bridge_source_id:
            linked_source_id = resolved.bridge_source_id
            linked_source_relative = resolved.bridge_source_relative
    return ResolvedWorkflowInputs(paths, linked_source_id, linked_source_relative)
