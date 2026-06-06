"""Test artifact routes behavior for NeuroCade."""

from pathlib import Path
import sys
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.routers.artifacts import download_case_archive, list_case_artifacts  # noqa: E402
from api_service.artifacts.service import artifact_download_path_for_output, resolve_artifact_file_for_user  # noqa: E402
from api_service.routers import artifacts as artifacts_module  # noqa: E402
from api_service.cases import operations as cases_module  # noqa: E402
from api_service.routers.cases import case_logs, case_runs, list_cases  # noqa: E402
from backend_common.case_storage import case_relative_prefix, case_storage_dir  # noqa: E402
from backend_common.db import Run, Artifact, ArtifactKind, Base, Case, RunStatus, Workspace  # noqa: E402
from backend_common import storage as storage_module  # noqa: E402
from tests.factories import seed_workspace_context  # noqa: E402

CASE_ID = "workspace-1__case-1"


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
    monkeypatch.setattr(storage_module.settings, "outputs_dir_override", tmp_path / "output")
    monkeypatch.setattr(cases_module.settings, "fs_data_root", tmp_path)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", tmp_path / "output")
    monkeypatch.setattr(artifacts_module.settings, "fs_data_root", tmp_path)
    monkeypatch.setattr(artifacts_module.settings, "outputs_dir_override", tmp_path / "output")

    context, workspace, cases = seed_workspace_context(
        db_session,
        workspace_id="workspace-1",
        workspace_name="primary-workspace",
        workspace_kind="shared",
        is_default_workspace=False,
        case_specs=(("case-1", "case-1"),),
    )
    case = cases[0]

    existing_rel = f"{case_relative_prefix(workspace.id, case.id)}/existing.mgz"
    missing_rel = f"{case_relative_prefix(workspace.id, case.id)}/missing.mgz"
    existing_path = tmp_path / existing_rel
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
            run_type="run_fastsurfer",
            runtime_job_id=case.id,
            result_json={},
        ),
    ])
    db_session.commit()

    return db_session, context


def test_list_case_artifacts_skips_missing_files(seeded_case):
    db_session, context = seeded_case

    artifacts = list_case_artifacts(CASE_ID, db=db_session, context=context)

    assert [artifact.name for artifact in artifacts] == ["existing.mgz"]


def test_artifact_download_path_for_readable_output_case_slug(seeded_case):
    db_session, context = seeded_case

    path = artifact_download_path_for_output(
        db_session,
        context,
        "output/workspaces/workspace-1/cases/case-1/existing.mgz",
    )

    assert path == "/artifacts/artifact-existing/download"


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


def test_list_case_artifacts_infers_uploaded_mask_as_segmentation(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    mask_rel = f"{case_relative_prefix(workspace.id, case.id)}/mask.mgz"
    mask_path = artifacts_module.settings.fs_data_root / mask_rel
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

    assert mask_artifact.metadata["volume_role"] == "segmentation"
    assert mask_artifact.metadata["lut"] == "binary"


def test_list_case_artifacts_does_not_auto_index_new_volume_when_artifacts_already_exist(seeded_case):
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
    assert "aparc.DKTatlas+aseg.deep.mgz" not in [artifact.name for artifact in artifacts]
    assert seg_artifact is None


def test_download_case_archive_returns_zip_with_case_contents(seeded_case):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    scripts_dir = case_storage_dir(artifacts_module.settings, workspace.id, case.id) / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "stdout.log").write_text("step 1\n", encoding="utf-8")

    response = download_case_archive(CASE_ID, db=db_session, context=context)
    archive_path = Path(response.path)

    with zipfile.ZipFile(archive_path, "r") as archive:
        names = set(archive.namelist())

    assert f"{case.title}/existing.mgz" in names
    assert f"{case.title}/scripts/stdout.log" in names
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

    other_case = Case(id="workspace-1__case-2", workspace_id=workspace.id, owner_user_id=context.user.id, title="case-2")
    db_session.add(other_case)
    db_session.flush()
    other_dir = case_storage_dir(artifacts_module.settings, workspace.id, other_case.id)
    other_dir.mkdir(parents=True, exist_ok=True)
    secret_path = other_dir / "secret.mgz"
    secret_path.write_bytes(b"secret")
    db_session.add(
        Artifact(
            id="artifact-cross-case",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="secret.mgz",
            relative_path=str(secret_path.resolve().relative_to(artifacts_module.settings.fs_data_root.resolve())),
            mime_type="application/octet-stream",
            size_bytes=6,
            metadata_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        resolve_artifact_file_for_user(db_session, context, "artifact-cross-case")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_cases_counts_only_existing_artifacts(seeded_case):
    db_session, context = seeded_case

    cases = await list_cases(db=db_session, context=context)

    assert len(cases) == 1
    assert cases[0].artifact_count == 1


@pytest.mark.asyncio
async def test_list_cases_can_filter_by_workspace(seeded_case):
    db_session, context = seeded_case

    cases = await list_cases(workspace_id="workspace-1", db=db_session, context=context)

    assert len(cases) == 1
    assert cases[0].workspace_id == "workspace-1"

    with pytest.raises(HTTPException) as exc_info:
        await list_cases(workspace_id="workspace-missing", db=db_session, context=context)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_cases_syncs_latest_run_status_from_runtime(seeded_case, monkeypatch):
    db_session, context = seeded_case

    async def fake_fetch_status(case_id: str, workspace_id: str):
        assert case_id == CASE_ID
        assert workspace_id == "workspace-1"
        return {"status": "finished"}

    monkeypatch.setattr("api_service.routers.cases.runtime_service.fetch_status", fake_fetch_status)

    cases = await list_cases(db=db_session, context=context)
    refreshed_run = db_session.get(Run, "run-1")

    assert cases[0].latest_run_status == "completed"
    assert refreshed_run is not None
    assert refreshed_run.status == RunStatus.completed


@pytest.mark.asyncio
async def test_case_runs_syncs_latest_run_status_from_runtime(seeded_case, monkeypatch):
    db_session, context = seeded_case

    async def fake_fetch_status(case_id: str, workspace_id: str):
        assert case_id == CASE_ID
        assert workspace_id == "workspace-1"
        return {"status": "running"}

    monkeypatch.setattr("api_service.routers.cases.runtime_service.fetch_status", fake_fetch_status)

    runs = await case_runs(CASE_ID, db=db_session, context=context)
    refreshed_run = db_session.get(Run, "run-1")

    assert len(runs) == 1
    assert runs[0].status == "running"
    assert refreshed_run is not None
    assert refreshed_run.status == RunStatus.running


@pytest.mark.asyncio
async def test_case_logs_reads_terminal_logs_from_canonical_storage(seeded_case, monkeypatch):
    db_session, context = seeded_case
    case = db_session.get(Case, CASE_ID)
    workspace = db_session.get(Workspace, "workspace-1")
    assert case is not None
    assert workspace is not None

    run = db_session.get(Run, "run-1")
    assert run is not None
    run.status = RunStatus.completed
    db_session.commit()

    scripts_dir = case_storage_dir(cases_module.settings, workspace.id, case.id) / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "stdout.log").write_text("step 1\rstep 2\n", encoding="utf-8")
    (scripts_dir / "stderr.log").write_text("warn\n", encoding="utf-8")

    async def fail_fetch_logs(_case_id: str, _workspace_id: str):
        raise AssertionError("Terminal case logs should be read from canonical storage, not the runtime status dir")

    monkeypatch.setattr(cases_module.runtime_service, "fetch_logs", fail_fetch_logs)

    payload = await case_logs(CASE_ID, db=db_session, context=context)

    assert "step 2" in payload["logs"]
    assert "--- STDERR ---" in payload["logs"]
    assert "warn" in payload["logs"]


@pytest.mark.asyncio
async def test_case_logs_falls_back_to_flat_sample_seed_logs(seeded_case, monkeypatch):
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
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "stdout.log").write_text("seed step\n", encoding="utf-8")
    (case_dir / "stderr.log").write_text("seed warn\n", encoding="utf-8")

    async def fail_fetch_logs(_case_id: str, _workspace_id: str):
        raise AssertionError("Terminal case logs should be read from canonical storage, not the runtime status dir")

    monkeypatch.setattr(cases_module.runtime_service, "fetch_logs", fail_fetch_logs)

    payload = await case_logs(CASE_ID, db=db_session, context=context)

    assert "seed step" in payload["logs"]
    assert "--- STDERR ---" in payload["logs"]
    assert "seed warn" in payload["logs"]
