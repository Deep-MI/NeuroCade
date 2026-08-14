"""Test artifact routes behavior for NeuroCade."""

import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.artifacts.service import artifact_download_path_for_output, resolve_artifact_file_for_user  # noqa: E402
from api_service.cases import operations as cases_module  # noqa: E402
from api_service.routers import artifacts as artifacts_module  # noqa: E402
from api_service.routers.artifacts import download_case_archive, list_case_artifacts  # noqa: E402
from api_service.routers.cases import case_logs, case_runs, list_cases  # noqa: E402
from api_service.runtime_tools.workflow_catalog import resolve_workflow  # noqa: E402
from api_service.runtime_tools.workflow_outputs import snapshot_workflow_outputs, write_output_baseline  # noqa: E402

from backend_common import storage as storage_module  # noqa: E402
from backend_common.case_storage import case_storage_dir, ensure_case_storage_layout  # noqa: E402
from backend_common.db import Artifact, ArtifactKind, AuditEvent, Base, Case, CaseEvent, Run, RunStatus, Workspace  # noqa: E402
from backend_common.run_logs import run_log_paths  # noqa: E402
from tests.factories import seed_workspace_context  # noqa: E402

CASE_ID = "case-1-id"


@pytest.fixture()
def db_session():
    """Provide an in-memory database session for route tests."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_case(db_session, monkeypatch, tmp_path):
    """Create a case with existing, missing, and queued-run artifacts."""
    monkeypatch.setattr(storage_module.settings, "fs_data_root", tmp_path)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", tmp_path)
    monkeypatch.setattr(artifacts_module.settings, "fs_data_root", tmp_path)

    context, workspace, cases = seed_workspace_context(
        db_session,
        workspace_id="workspace-1",
        workspace_name="primary-workspace",
        workspace_kind="shared",
        is_default_workspace=False,
        case_specs=((CASE_ID, "case-1"),),
    )
    case = cases[0]
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)

    existing_rel = "existing.mgz"
    missing_rel = "missing.mgz"
    existing_path = case_dir / existing_rel
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_bytes(b"mgz")

    db_session.add_all([
        Artifact(
            id="artifact-existing",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="existing.mgz",
            relative_path=existing_rel,
            mime_type="application/octet-stream",
            size_bytes=3,
            metadata_json={"volume_role": "intensity"},
        ),
        Artifact(
            id="artifact-missing",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="missing.mgz",
            relative_path=missing_rel,
            mime_type="application/octet-stream",
            size_bytes=0,
            metadata_json={"volume_role": "segmentation"},
        ),
        Run(
            id="run-1",
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.queued,
            run_type="fastsurfer_full",
            job_id=case.id,
            result_json={},
        ),
    ])
    db_session.commit()

    return db_session, context


def test_list_case_artifacts_skips_missing_files(seeded_case):
    db_session, context = seeded_case

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)

    assert [artifact.name for artifact in artifacts] == ["existing.mgz"]
    assert db_session.get(Artifact, "artifact-missing") is not None


def test_list_case_artifacts_prunes_stable_missing_rows_and_preserves_events(seeded_case):
    db_session, context = seeded_case
    run = db_session.get(Run, "run-1")
    assert run is not None
    run.status = RunStatus.completed
    audit_event = AuditEvent(
        user_id=context.user.id,
        case_id=CASE_ID,
        artifact_id="artifact-missing",
        action="artifact.downloaded",
        details_json={},
    )
    case_event = CaseEvent(
        case_id=CASE_ID,
        workspace_id="workspace-1",
        user_id=context.user.id,
        artifact_id="artifact-missing",
        event_type="artifact.created",
        details_json={},
    )
    db_session.add_all([audit_event, case_event])
    db_session.commit()

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)

    assert [artifact.name for artifact in artifacts] == ["existing.mgz"]
    assert db_session.get(Artifact, "artifact-missing") is None
    db_session.refresh(audit_event)
    db_session.refresh(case_event)
    assert audit_event.artifact_id is None
    assert case_event.artifact_id is None


def test_artifact_download_path_for_readable_output_case(seeded_case):
    db_session, context = seeded_case

    path = artifact_download_path_for_output(
        db_session,
        context,
        "workspaces/primary-workspace/cases/case-1/existing.mgz",
    )

    assert path == "/artifacts/artifact-existing/download"


def test_artifact_download_closes_read_transaction_before_audit(seeded_case, monkeypatch):
    db_session, context = seeded_case
    transaction_states: list[bool] = []
    monkeypatch.setattr(
        artifacts_module,
        "log_event",
        lambda db, *_args, **_kwargs: transaction_states.append(db.in_transaction()),
    )

    response = artifacts_module.download_artifact("artifact-existing", db=db_session, context=context)

    assert str(response.path).endswith("existing.mgz")
    assert transaction_states == [False]


def test_list_case_artifacts_does_not_index_unregistered_files(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    db_session.query(Artifact).filter(Artifact.case_id == CASE_ID).delete()
    db_session.commit()

    restored_path = case_storage_dir(artifacts_module.settings, workspace.id, case.id) / "restored.nii.gz"
    restored_path.write_bytes(b"nii")

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    restored_artifact = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == CASE_ID, Artifact.name == "restored.nii.gz")
        .one_or_none()
    )

    assert "restored.nii.gz" not in [artifact.name for artifact in artifacts]
    assert restored_artifact is None


def test_list_case_artifacts_preserves_stored_volume_role(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    mask_rel = "mask.mgz"
    mask_path = case_storage_dir(artifacts_module.settings, workspace.id, case.id) / mask_rel
    mask_path.write_bytes(b"mask")
    db_session.add(Artifact(
        id="artifact-mask-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="mask.mgz",
        relative_path=mask_rel,
        mime_type="application/octet-stream",
        size_bytes=4,
        metadata_json={"volume_role": "intensity"},
    ))
    db_session.commit()

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    mask_artifact = next(artifact for artifact in artifacts if artifact.name == "mask.mgz")

    assert mask_artifact.metadata["volume_role"] == "intensity"
    assert "lut" not in mask_artifact.metadata


def test_list_case_artifacts_indexes_typed_catalog_outputs_while_run_is_active(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    run = db_session.get(Run, "run-1")
    assert case is not None
    assert workspace is not None
    assert run is not None
    run.run_type = "fastsurfer_full"
    db_session.commit()

    case_dir = case_storage_dir(artifacts_module.settings, workspace.id, case.id)
    workflow = resolve_workflow(run.run_type)
    segmentation = case_dir / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
    surface = case_dir / "surf" / "lh.white"
    report = case_dir / "stats" / "aseg+DKT.VINN.stats"
    surface.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    surface.write_bytes(b"old surface")
    report.write_bytes(b"old stats")
    run_dir = case_dir / ".runs" / run.id
    run_dir.mkdir(parents=True)
    write_output_baseline(
        run_dir,
        snapshot_workflow_outputs(
            workflow,
            tuple(case_dir / output.path for output in workflow.outputs),
        ),
    )
    for path, contents in ((segmentation, b"seg"), (surface, b"new surface")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    by_name = {artifact.name: artifact for artifact in artifacts}

    assert by_name[segmentation.name].metadata["output_type"] == "segmentation_volume"
    assert by_name[segmentation.name].metadata["volume_role"] == "segmentation"
    assert by_name[segmentation.name].metadata["lut"] == "freesurfer"
    assert by_name[segmentation.name].metadata["output_state"] == "created"
    assert by_name[segmentation.name].metadata["run_id"] == run.id
    assert by_name[surface.name].metadata["output_type"] == "surface"
    assert by_name[surface.name].metadata["layer_role"] == "surface"
    assert by_name[surface.name].metadata["output_state"] == "modified"
    assert by_name[report.name].metadata["output_type"] == "other"
    assert by_name[report.name].metadata["output_state"] == "preexisting"
    assert "run_id" not in by_name[report.name].metadata

    run.status = RunStatus.completed
    run.result_json = {
        "outputs": [
            {"name": output.name, "state": "created"}
            for output in resolve_workflow(run.run_type).outputs
        ]
    }
    final_callosum = case_dir / "mri" / "callosum.CC.orig.mgz"
    final_callosum.parent.mkdir(parents=True, exist_ok=True)
    final_callosum.write_bytes(b"callosum")
    db_session.commit()

    completed_artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    callosum = next(artifact for artifact in completed_artifacts if artifact.name == final_callosum.name)
    assert callosum.metadata["output_type"] == "segmentation_volume"
    assert callosum.metadata["output_state"] == "created"


def test_manual_output_name_override_sets_display_metadata_without_renaming_file(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    run = db_session.get(Run, "run-1")
    assert case is not None
    assert workspace is not None
    assert run is not None
    run.run_type = "fastsurfer_fast"
    run.status = RunStatus.completed
    run.input_json = {
        "output_name_overrides": {"whole_brain_segmentation": "Baseline segmentation"},
    }
    run.result_json = {
        "outputs": [{"name": "whole_brain_segmentation", "state": "created"}],
    }
    segmentation = case_storage_dir(artifacts_module.settings, workspace.id, case.id) / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
    segmentation.parent.mkdir(parents=True, exist_ok=True)
    segmentation.write_bytes(b"segmentation")
    db_session.commit()

    first = list_case_artifacts(CASE_ID, db=db_session, context=context)
    second = list_case_artifacts(CASE_ID, db=db_session, context=context)

    for artifacts in (first, second):
        artifact = next(item for item in artifacts if item.name == segmentation.name)
        assert artifact.metadata["display_name"] == "Baseline segmentation"
        assert artifact.metadata["output_name"] == "whole_brain_segmentation"


def test_catalog_retypes_existing_outputs_not_declared_by_latest_workflow(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    run = db_session.get(Run, "run-1")
    assert case is not None
    assert workspace is not None
    assert run is not None
    run.run_type = "fastsurfer_fast"
    run.status = RunStatus.completed

    case_dir = case_storage_dir(artifacts_module.settings, workspace.id, case.id)
    callosum_path = case_dir / "mri" / "callosum.CC.upright.mgz"
    callosum_path.parent.mkdir(parents=True, exist_ok=True)
    callosum_path.write_bytes(b"callosum")
    db_session.commit()

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    callosum = next(artifact for artifact in artifacts if artifact.name == callosum_path.name)

    assert callosum.metadata["source"] == "workflow-catalog"
    assert callosum.metadata["workflow_id"] == "fastsurfer_full"
    assert callosum.metadata["output_type"] == "segmentation_volume"
    assert callosum.metadata["volume_role"] == "segmentation"


def test_catalog_refresh_preserves_original_output_producer(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    run = db_session.get(Run, "run-1")
    assert case is not None
    assert workspace is not None
    assert run is not None
    run.run_type = "fastsurfer_fast"
    run.status = RunStatus.completed

    case_dir = case_storage_dir(artifacts_module.settings, workspace.id, case.id)
    callosum_path = case_dir / "mri" / "callosum.CC.orig.mgz"
    callosum_path.parent.mkdir(parents=True, exist_ok=True)
    callosum_path.write_bytes(b"callosum")
    relative_path = str(callosum_path.relative_to(case_dir))
    db_session.add(
        Artifact(
            id="artifact-callosum-produced",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name=callosum_path.name,
            relative_path=relative_path,
            mime_type="application/octet-stream",
            size_bytes=callosum_path.stat().st_size,
            metadata_json={
                "source": "workflow-output",
                "workflow_id": "fastsurfer_full",
                "output_name": "callosum_original_segmentation",
                "output_type": "segmentation_volume",
                "run_id": "older-full-run",
                "output_state": "created",
                "observed_run_id": "older-full-run",
            },
        )
    )
    db_session.commit()

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    callosum = next(artifact for artifact in artifacts if artifact.name == callosum_path.name)

    assert callosum.metadata["source"] == "workflow-output"
    assert callosum.metadata["run_id"] == "older-full-run"
    assert callosum.metadata["output_state"] == "created"


def test_list_case_artifacts_indexes_only_config_declared_new_volumes(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    seg_path = case_storage_dir(artifacts_module.settings, workspace.id, case.id) / "mri" / "aparc.DKTatlas+aseg.deep.mgz"
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    seg_path.write_bytes(b"seg")

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)
    seg_artifact = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == CASE_ID, Artifact.name == "aparc.DKTatlas+aseg.deep.mgz")
        .one_or_none()
    )

    assert "existing.mgz" in [artifact.name for artifact in artifacts]
    assert "aparc.DKTatlas+aseg.deep.mgz" in [artifact.name for artifact in artifacts]
    assert seg_artifact is not None
    assert seg_artifact.metadata_json["output_type"] == "segmentation_volume"


def test_download_case_archive_returns_zip_with_case_contents(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    scripts_dir = case_storage_dir(artifacts_module.settings, workspace.id, case.id) / "scripts" / "runs" / "run-1"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "stdout.log").write_text("step 1\n", encoding="utf-8")

    response = download_case_archive(CASE_ID, db=db_session, context=context)
    archive_path = Path(response.path)

    with zipfile.ZipFile(archive_path, "r") as archive:
        names = set(archive.namelist())

    assert f"{case.title}/existing.mgz" in names
    assert f"{case.title}/scripts/runs/run-1/stdout.log" in names
    assert f"{case.title}/missing.mgz" not in names


def test_download_case_archive_skips_symlinked_files_outside_case(seeded_case, tmp_path):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    outside_file = tmp_path / "outside-secret.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    case_dir = case_storage_dir(artifacts_module.settings, workspace.id, case.id)
    (case_dir / "leaked.txt").symlink_to(outside_file)

    response = download_case_archive(CASE_ID, db=db_session, context=context)
    archive_path = Path(response.path)

    with zipfile.ZipFile(archive_path, "r") as archive:
        names = set(archive.namelist())

    assert f"{case.title}/leaked.txt" not in names


def test_artifact_download_rejects_case_row_pointing_outside_case(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    other_case = Case(id="case-2-id", workspace_id=workspace.id, owner_user_id=context.user.id, title="case-2")
    db_session.add(other_case)
    db_session.flush()
    other_dir = ensure_case_storage_layout(artifacts_module.settings, other_case, workspace)
    secret_path = other_dir / "secret.mgz"
    secret_path.write_bytes(b"secret")
    db_session.add(
        Artifact(
            id="artifact-cross-case",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="secret.mgz",
            relative_path=secret_path.name,
            mime_type="application/octet-stream",
            size_bytes=6,
            metadata_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        resolve_artifact_file_for_user(db_session, context, "artifact-cross-case")

    assert exc_info.value.status_code == 404


def test_list_cases_counts_only_existing_artifacts(seeded_case):
    db_session, context = seeded_case

    cases = list_cases(db=db_session, context=context)

    assert len(cases) == 1
    assert cases[0].artifact_count == 1


def test_list_cases_can_filter_by_workspace(seeded_case):
    db_session, context = seeded_case

    cases = list_cases(workspace_id="workspace-1", db=db_session, context=context)

    assert len(cases) == 1
    assert cases[0].workspace_id == "workspace-1"

    with pytest.raises(HTTPException) as exc_info:
        list_cases(workspace_id="workspace-missing", db=db_session, context=context)
    assert exc_info.value.status_code == 404


def test_list_cases_reads_durable_run_status(seeded_case):
    db_session, context = seeded_case

    cases = list_cases(db=db_session, context=context)
    refreshed_run = db_session.get(Run, "run-1")

    assert cases[0].latest_run_status == "queued"
    assert refreshed_run is not None
    assert refreshed_run.status == RunStatus.queued


def test_case_runs_reads_durable_status(seeded_case):
    db_session, context = seeded_case

    runs = case_runs(CASE_ID, db=db_session, context=context)
    refreshed_run = db_session.get(Run, "run-1")

    assert len(runs) == 1
    assert runs[0].status == "queued"
    assert refreshed_run is not None
    assert refreshed_run.status == RunStatus.queued


def test_case_logs_reads_run_specific_logs(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    run = db_session.get(Run, "run-1")
    assert run is not None
    run.status = RunStatus.completed
    db_session.commit()

    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    stdout_path, stderr_path = run_log_paths(case_dir, run.id)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text("step 1\rstep 2\n", encoding="utf-8")
    stderr_path.write_text("warn\n", encoding="utf-8")

    payload = case_logs(CASE_ID, db=db_session, context=context)

    assert "step 2" in payload["logs"]
    assert "--- STDERR ---" in payload["logs"]
    assert "warn" in payload["logs"]
