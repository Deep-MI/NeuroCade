"""Build and register workspace batch logs, manifests, and summaries."""

from __future__ import annotations

import csv
import json
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.workspace_batch.queries import (
    analysis_id_from_run,
    artifacts_for_run,
    child_runs_for_run,
    command_from_run,
    report_name_from_run,
    run_counts,
    selected_cases_for_run,
)
from backend_common.artifact_upsert import insert_artifact_if_missing
from backend_common.case_storage import (
    case_storage_dir,
    ensure_workspace_analysis_storage_layout,
    workspace_analysis_relative_prefix,
)
from backend_common.db import Artifact, ArtifactKind, Case, Run, RunStatus, Workspace
from backend_common.scan import VOLUME_SUFFIXES


def write_case_log(workspace: Workspace, case: Case, run_id: str, content: str) -> Path:
    """Write a workspace batch log for one case and return the log path."""
    case_dir = case_storage_dir(settings, workspace.id, case.id)
    log_dir = case_dir / "workspace-batch" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "batch.log"
    log_path.write_text(content, encoding="utf-8")
    return log_path


def ensure_case_log_artifact(
    db: Session,
    workspace: Workspace,
    case: Case,
    run_id: str,
    log_path: Path,
    *,
    run_type: str,
) -> None:
    """Create or refresh the artifact record for a case batch log."""
    relative_path = str(log_path.resolve().relative_to(settings.fs_data_root.resolve()))
    artifact = (
        db.query(Artifact)
        .filter(Artifact.case_id == case.id, Artifact.relative_path == relative_path)
        .one_or_none()
    )
    if artifact is None:
        db.add(
            Artifact(
                case_id=case.id,
                workspace_id=workspace.id,
                kind=ArtifactKind.log,
                name=log_path.name,
                relative_path=relative_path,
                mime_type="text/plain",
                size_bytes=log_path.stat().st_size,
                metadata_json={
                    "run_id": run_id,
                    "run_type": run_type,
                    "scope": "workspace",
                },
            )
        )
        return
    artifact.size_bytes = log_path.stat().st_size
    artifact.metadata_json = {
        **(artifact.metadata_json or {}),
        "run_id": run_id,
        "run_type": run_type,
        "scope": "workspace",
    }


def workspace_artifact_kind_for_path(path: Path) -> ArtifactKind:
    """Classify a workspace analysis artifact from its filename suffix."""
    lowered = path.name.lower()
    if lowered.endswith(VOLUME_SUFFIXES):
        return ArtifactKind.volume
    if lowered.endswith((".log", ".txt")):
        return ArtifactKind.log
    if lowered.endswith((".json", ".csv", ".tsv", ".md", ".html")):
        return ArtifactKind.report
    return ArtifactKind.derived


def result_text_has_execution_error(result: str) -> bool:
    """Return whether command output begins with a known execution error."""
    lowered = result.lstrip().lower()
    return (
        lowered.startswith("error executing")
        or lowered.startswith("error:")
        or lowered.startswith("an unexpected error occurred")
    )


def sync_workspace_analysis_artifacts(db: Session, parent_run: Run, analysis_dir: Path) -> list[Artifact]:
    """Ensure database artifacts match files produced for a workspace analysis."""
    workspace = db.get(Workspace, parent_run.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    relative_prefix = workspace_analysis_relative_prefix(workspace.id, analysis_id_from_run(parent_run))
    existing = {
        artifact.relative_path: artifact
        for artifact in artifacts_for_run(db, parent_run)
    }
    ensured: list[Artifact] = []
    for path in sorted(analysis_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(analysis_dir).as_posix()
        artifact_rel_path = f"{relative_prefix}/{rel_path}"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        artifact = existing.get(artifact_rel_path)
        if artifact is None:
            artifact = (
                db.query(Artifact)
                .filter(
                    Artifact.workspace_id == workspace.id,
                    Artifact.case_id.is_(None),
                    Artifact.relative_path == artifact_rel_path,
                )
                .order_by(Artifact.created_at.desc())
                .first()
            )
        if artifact is None:
            artifact = insert_artifact_if_missing(
                db,
                {
                    "case_id": None,
                    "workspace_id": workspace.id,
                    "kind": workspace_artifact_kind_for_path(path),
                    "name": path.name,
                    "relative_path": artifact_rel_path,
                    "mime_type": mime_type,
                    "size_bytes": path.stat().st_size,
                    "metadata_json": {
                        "run_id": parent_run.id,
                        "analysis_id": analysis_id_from_run(parent_run),
                        "scope": "workspace",
                        "run_type": parent_run.run_type,
                        "report_name": report_name_from_run(parent_run),
                    },
                },
                case_scoped=False,
            )
        if artifact is not None:
            artifact.kind = workspace_artifact_kind_for_path(path)
            artifact.name = path.name
            artifact.mime_type = mime_type
            artifact.size_bytes = path.stat().st_size
            artifact.metadata_json = {
                **(artifact.metadata_json or {}),
                "run_id": parent_run.id,
                "analysis_id": analysis_id_from_run(parent_run),
                "scope": "workspace",
                "run_type": parent_run.run_type,
                "report_name": report_name_from_run(parent_run),
            }
            ensured.append(artifact)
    return ensured


def write_run_files(db: Session, parent_run: Run) -> Path:
    """Write workspace run manifest and summary files."""
    workspace = db.get(Workspace, parent_run.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    analysis_id = analysis_id_from_run(parent_run)
    report_name = report_name_from_run(parent_run)
    command = command_from_run(parent_run)
    runs = child_runs_for_run(db, parent_run.id)
    selected_cases = selected_cases_for_run(db, parent_run)
    analysis_dir = ensure_workspace_analysis_storage_layout(settings, workspace.id, analysis_id)

    counts = run_counts(runs)
    case_rows = []
    for run in runs:
        case = db.get(Case, run.case_id)
        case_rows.append(
            {
                "run_id": run.id,
                "case_id": run.case_id,
                "case_title": case.title if case is not None else run.case_id,
                "status": run.status.value,
                "external_task_id": run.external_task_id,
                "error_message": run.error_message,
                "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            }
        )
    if not case_rows and selected_cases:
        case_rows = [
            {
                "run_id": None,
                "case_id": case.id,
                "case_title": case.title,
                "status": parent_run.status.value,
                "external_task_id": str((parent_run.result_json or {}).get("external_task_id") or ""),
                "error_message": None,
                "updated_at": parent_run.updated_at.isoformat() if parent_run.updated_at else None,
            }
            for case in selected_cases
        ]

    manifest = {
        "run_id": parent_run.id,
        "workspace_id": parent_run.workspace_id,
        "status": parent_run.status.value,
        "run_type": parent_run.run_type,
        "command": command,
        "report_name": report_name,
        "analysis_id": analysis_id,
        "selected_case_count": len(selected_cases),
        "counts": counts,
        "cases": case_rows,
    }

    manifest_path = analysis_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    csv_path = analysis_dir / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "case_id",
                "case_title",
                "status",
                "external_task_id",
                "error_message",
                "updated_at",
            ],
        )
        writer.writeheader()
        for row in case_rows:
            writer.writerow(row)

    markdown_lines = [
        f"# Workspace run: {report_name}",
        "",
        f"Run ID: `{parent_run.id}`",
        f"Status: `{parent_run.status.value}`",
        f"Action: `{parent_run.run_type}`",
        "",
        "```bash",
        command,
        "```",
        "",
        f"Selected cases: {len(selected_cases)}",
        f"Queued: {counts.get(RunStatus.queued.value, 0)}",
        f"Running: {counts.get(RunStatus.running.value, 0)}",
        f"Completed: {counts.get(RunStatus.completed.value, 0)}",
        f"Failed: {counts.get(RunStatus.failed.value, 0)}",
        f"Canceled: {counts.get(RunStatus.canceled.value, 0)}",
        "",
    ]
    for row in case_rows:
        markdown_lines.extend(
            [
                f"## {row['case_title']} ({row['case_id']})",
                "",
                f"Status: `{row['status']}`",
                "",
            ]
        )
        if row["error_message"]:
            markdown_lines.extend([f"Error: {row['error_message']}", ""])
    markdown_path = analysis_dir / "summary.md"
    markdown_path.write_text("\n".join(markdown_lines), encoding="utf-8")

    parent_run.result_json = {
        **(parent_run.result_json or {}),
        "command": command,
        "report_name": report_name,
        "analysis_id": analysis_id,
        "counts": counts,
        "selected_case_count": len(selected_cases),
    }
    return analysis_dir
