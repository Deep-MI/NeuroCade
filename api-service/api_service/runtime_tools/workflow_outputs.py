"""Index catalog-declared workflow outputs as they become available."""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from sqlalchemy.orm import Session

from api_service.file_utils import safe_write_json
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow, WorkflowOutput, resolve_workflow, workflows
from backend_common.artifact_classification import classify_artifact
from backend_common.artifact_upsert import insert_artifact_if_missing
from backend_common.db import Artifact, Case, Run, RunStatus
from backend_common.run_statuses import ACTIVE_RUN_STATUSES
from backend_common.settings import Settings

OutputState = Literal["created", "modified", "preexisting", "missing"]
logger = logging.getLogger(__name__)


class OutputFingerprint(TypedDict):
    """Small filesystem identity used to classify an output after a run."""

    size_bytes: int
    mtime_ns: int
    inode: int


OutputBaseline = dict[str, OutputFingerprint | None]


def _fingerprint(path: Path) -> OutputFingerprint | None:
    try:
        file_stat = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        return None
    return {
        "size_bytes": file_stat.st_size,
        "mtime_ns": file_stat.st_mtime_ns,
        "inode": file_stat.st_ino,
    }


def snapshot_workflow_outputs(
    workflow: NeuroimagingWorkflow,
    output_paths: tuple[Path, ...],
) -> OutputBaseline:
    """Snapshot declared outputs immediately before workflow execution."""
    return {output.name: _fingerprint(path) for output, path in zip(workflow.outputs, output_paths, strict=True)}


def classify_output(path: Path, before: OutputFingerprint | None) -> OutputState:
    """Classify a declared output relative to its pre-run filesystem state."""
    after = _fingerprint(path)
    if after is None:
        return "missing"
    if before is None:
        return "created"
    return "preexisting" if after == before else "modified"


def write_output_baseline(run_dir: Path, baseline: OutputBaseline) -> None:
    """Persist the baseline so live artifact polling can classify existing files."""
    path = run_dir / "output-baseline.json"
    safe_write_json(str(path), {"outputs": baseline})
    if not path.is_file():
        raise RuntimeError(f"Could not persist workflow output baseline: {path}")


def read_output_baseline(case_dir: Path, run_id: str) -> OutputBaseline | None:
    """Load a running workflow's output baseline when it is available."""
    path = case_dir / ".runs" / run_id / "output-baseline.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_outputs = payload.get("outputs") if isinstance(payload, dict) else None
    if not isinstance(raw_outputs, dict):
        return None
    baseline: OutputBaseline = {}
    for name, value in raw_outputs.items():
        if not isinstance(name, str):
            return None
        if value is None:
            baseline[name] = None
            continue
        if not isinstance(value, dict) or not all(isinstance(value.get(key), int) for key in ("size_bytes", "mtime_ns", "inode")):
            return None
        baseline[name] = {
            "size_bytes": value["size_bytes"],
            "mtime_ns": value["mtime_ns"],
            "inode": value["inode"],
        }
    return baseline


def _output_metadata(
    workflow: NeuroimagingWorkflow,
    run_id: str,
    output: WorkflowOutput,
    *,
    include_run_provenance: bool,
) -> dict:
    metadata = dict(output.metadata)
    metadata.update(
        {
            "source": "workflow-output" if include_run_provenance else "workflow-catalog",
            "workflow_id": workflow.id,
            "output_name": output.name,
            "output_type": output.type,
        }
    )
    if include_run_provenance:
        metadata["run_id"] = run_id
    return metadata


def _retain_existing_producer(
    metadata: dict,
    existing: dict,
    *,
    state: OutputState | None,
) -> dict:
    """Keep the original producing run when a later run only observes a file."""
    if existing.get("source") != "workflow-output" or not existing.get("run_id"):
        return metadata
    for key in ("source", "workflow_id", "output_name", "run_id", "display_name"):
        if key in existing:
            metadata[key] = existing[key]
    if state is None:
        for key in ("output_state", "observed_run_id"):
            if key in existing:
                metadata[key] = existing[key]
    return metadata


def index_workflow_outputs(
    db: Session,
    settings: Settings,
    *,
    case: Case,
    workflow: NeuroimagingWorkflow,
    run_id: str,
    active: bool = False,
    include_run_provenance: bool = True,
    output_states: dict[str, OutputState] | None = None,
) -> list[Artifact]:
    """Upsert existing files declared by one workflow invocation."""
    from backend_common.case_storage import case_storage_dir

    case_dir = case_storage_dir(settings, case.workspace_id, case.id).resolve()
    indexed: list[Artifact] = []
    baseline = read_output_baseline(case_dir, run_id) if active else None
    run = db.get(Run, run_id) if run_id else None
    raw_overrides = (run.input_json or {}).get("output_name_overrides") if run is not None else None
    output_name_overrides = raw_overrides if isinstance(raw_overrides, dict) else {}

    for output in workflow.outputs:
        relative_text = output.path.replace("{run_id}", run_id)
        candidate = case_dir / Path(*PurePosixPath(relative_text).parts)
        try:
            candidate_stat = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(candidate_stat.st_mode) or not stat.S_ISREG(candidate_stat.st_mode):
            continue
        path = candidate.resolve()
        if case_dir not in path.parents:
            continue
        relative_storage_path = str(path.relative_to(case_dir))

        state = output_states.get(output.name) if output_states is not None else None
        if state is None and baseline is not None:
            state = classify_output(candidate, baseline.get(output.name))
        produced_in_run = include_run_provenance and state != "preexisting"
        metadata = _output_metadata(
            workflow,
            run_id,
            output,
            include_run_provenance=produced_in_run,
        )
        classification = classify_artifact(path, declared_type=output.type, metadata=metadata)
        if classification is None:
            continue
        if state is not None:
            classification.metadata["output_state"] = state
            classification.metadata["observed_run_id"] = run_id
        display_name = output_name_overrides.get(output.name)
        if produced_in_run and isinstance(display_name, str) and display_name:
            classification.metadata["display_name"] = display_name
        values = {
            "case_id": case.id,
            "workspace_id": case.workspace_id,
            "kind": classification.kind,
            "name": path.name,
            "relative_path": relative_storage_path,
            "mime_type": classification.mime_type,
            "size_bytes": candidate_stat.st_size,
            "metadata_json": classification.metadata,
        }
        artifact = insert_artifact_if_missing(db, values, case_scoped=True)
        if artifact is None:
            continue
        if not produced_in_run:
            values["metadata_json"] = _retain_existing_producer(
                classification.metadata,
                dict(artifact.metadata_json or {}),
                state=state,
            )
        # A prior generic scan may already have registered the path. The catalog
        # is authoritative for workflow output semantics and current file size.
        artifact.kind = values["kind"]
        artifact.name = values["name"]
        artifact.mime_type = values["mime_type"]
        artifact.size_bytes = values["size_bytes"]
        artifact.metadata_json = values["metadata_json"]
        indexed.append(artifact)

    db.flush()
    return indexed


def index_case_catalog_outputs(db: Session, settings: Settings, case: Case) -> list[Artifact]:
    """Index final case files declared by any catalog workflow."""
    indexed: list[Artifact] = []
    seen_paths: set[str] = set()
    for workflow in workflows():
        unique_outputs = []
        for output in workflow.outputs:
            if "{run_id}" in output.path or output.path in seen_paths:
                continue
            seen_paths.add(output.path)
            unique_outputs.append(output)
        if not unique_outputs:
            continue
        catalog_view = workflow.model_copy(update={"outputs": unique_outputs})
        indexed.extend(
            index_workflow_outputs(
                db,
                settings,
                case=case,
                workflow=catalog_view,
                run_id="",
                include_run_provenance=False,
            )
        )
    return indexed


def _result_output_states(run: Run) -> dict[str, OutputState]:
    result = run.result_json if isinstance(run.result_json, dict) else {}
    outputs = result.get("outputs")
    if not isinstance(outputs, list):
        return {}
    valid_states = {"created", "modified", "preexisting", "missing"}
    states: dict[str, OutputState] = {}
    for output in outputs:
        if not isinstance(output, dict):
            continue
        name = output.get("name")
        state = output.get("state")
        if isinstance(name, str) and state in valid_states:
            states[name] = state
    return states


def index_latest_case_workflow_outputs(db: Session, settings: Settings, case: Case) -> list[Artifact]:
    """Index files produced so far by the latest catalog-defined case run."""
    run = db.query(Run).filter(Run.case_id == case.id).order_by(Run.created_at.desc(), Run.id.desc()).first()
    if run is None:
        return index_case_catalog_outputs(db, settings, case)
    try:
        definition = (run.input_json or {}).get("workflow_definition")
        workflow = (
            NeuroimagingWorkflow.model_validate(definition)
            if isinstance(definition, dict)
            else resolve_workflow(run.run_type, settings=settings, user_id=run.created_by_user_id)
        )
    except (ValueError, TypeError):
        return index_case_catalog_outputs(db, settings, case)
    if run.status in ACTIVE_RUN_STATUSES:
        return index_workflow_outputs(
            db,
            settings,
            case=case,
            workflow=workflow,
            run_id=run.id,
            active=True,
        )

    indexed = index_case_catalog_outputs(db, settings, case)
    output_states = _result_output_states(run)
    if run.status == RunStatus.completed or output_states:
        indexed.extend(
            index_workflow_outputs(
                db,
                settings,
                case=case,
                workflow=workflow,
                run_id=run.id,
                output_states=output_states or None,
            )
        )
    return indexed


def index_all_case_workflow_outputs(db: Session, settings: Settings) -> list[Artifact]:
    """Index catalog-declared outputs for every case during startup recovery."""
    indexed: list[Artifact] = []
    for case in db.query(Case).order_by(Case.created_at.asc(), Case.id.asc()).all():
        try:
            indexed.extend(index_latest_case_workflow_outputs(db, settings, case))
        except FileNotFoundError:
            logger.warning("Skipping workflow output recovery for case %s because its storage manifest is missing", case.id)
    return indexed
