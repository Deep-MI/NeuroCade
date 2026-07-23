"""Serialize case domain rows into API response schemas."""

from __future__ import annotations

from api_service.artifacts.service import serialize_artifact
from api_service.schemas import CaseDetail, CaseSummary, RunSummary
from backend_common.db import Artifact, AssistantThread, Case, RoleEnum, Run


def _metadata_list(values: object) -> list[str]:
    """Return stored metadata labels as a stable list for response payloads."""
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def serialize_run_summary(run: Run) -> RunSummary:
    """Build the public run summary schema."""
    return RunSummary(
        id=run.id,
        case_id=run.case_id,
        workspace_id=run.workspace_id,
        status=run.status.value,
        run_type=run.run_type,
        created_at=run.created_at,
        updated_at=run.updated_at,
        error_message=run.error_message,
    )


def serialize_case_summary(
    case: Case,
    role: RoleEnum,
    *,
    thread: AssistantThread | None,
    latest_run: Run | None,
    artifact_count: int,
) -> CaseSummary:
    """Build the public case-list summary schema."""
    return CaseSummary(
        id=case.id,
        title=case.title,
        description=case.description,
        modalities=_metadata_list(case.modalities_json),
        tags=_metadata_list(case.tags_json),
        notes=case.notes,
        role=role.value,
        workspace_id=case.workspace_id,
        thread_id=thread.thread_key if thread else None,
        latest_run_status=latest_run.status.value if latest_run else None,
        artifact_count=artifact_count,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def serialize_case_detail(
    case: Case,
    role: RoleEnum,
    *,
    thread: AssistantThread | None,
    artifacts: list[Artifact],
    runs: list[Run],
) -> CaseDetail:
    """Build the public case detail schema."""
    return CaseDetail(
        id=case.id,
        title=case.title,
        description=case.description,
        modalities=_metadata_list(case.modalities_json),
        tags=_metadata_list(case.tags_json),
        notes=case.notes,
        role=role.value,
        workspace_id=case.workspace_id,
        thread_id=thread.thread_key if thread else None,
        artifacts=[serialize_artifact(artifact) for artifact in artifacts],
        runs=[serialize_run_summary(run) for run in runs],
    )
