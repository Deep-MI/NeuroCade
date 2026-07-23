"""Test admin reset behavior for NeuroCade."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend_common import sample_seed as sample_seed_module  # noqa: E402
from backend_common.admin_reset import purge_workspace, reset_sample_case_for_user  # noqa: E402
from backend_common.case_storage import case_storage_dir  # noqa: E402
from backend_common.db import (  # noqa: E402
    Artifact,
    ArtifactKind,
    AssistantCheckpoint,
    AssistantMessage,
    AssistantScope,
    AssistantThread,
    AuditEvent,
    Base,
    Case,
    RoleEnum,
    Run,
    RunStatus,
    User,
    Workspace,
    WorkspaceMembership,
)


@pytest.fixture()
def db_session():
    """Create an in-memory database session for reset tests."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _settings(tmp_path: Path) -> SimpleNamespace:
    """Build filesystem settings rooted in the pytest temp directory."""
    fs_data_root = tmp_path / "fs-data"
    outputs_dir = fs_data_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        fs_data_root=fs_data_root,
        outputs_dir=outputs_dir,
    )


def test_purge_workspace_deletes_cases_records_and_storage(db_session, tmp_path):
    settings = _settings(tmp_path)
    user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
    workspace = Workspace(
        id="ws-1",
        owner_user_id=user.id,
        name="ws-1",
        kind="personal",
        is_default=True,
        status="active",
    )
    case = Case(
        id="ws-1__case-1",
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title="case-1",
    )
    assistant_thread = AssistantThread(
        id="assistant-thread-1",
        thread_key="thread-key-1",
        scope_type=AssistantScope.case,
        workspace_id=workspace.id,
        case_id=case.id,
        created_by_user_id=user.id,
        provider_name="openai-compatible",
        model_name="model",
    )
    db_session.add_all([user, workspace, case, assistant_thread])
    db_session.flush()

    db_session.add_all([
        WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id),
        Run(
            id="run-1",
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            status=RunStatus.completed,
            run_type="run",
            runtime_job_id=case.id,
            result_json={},
        ),
        Artifact(
            id="artifact-1",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.log,
            name="stdout.log",
            relative_path=f"output/workspaces/{workspace.id}/cases/case-1/scripts/stdout.log",
            size_bytes=1,
            metadata_json={},
        ),
        AuditEvent(user_id=user.id, case_id=case.id, artifact_id="artifact-1", action="case.viewed", details_json={}),
        AssistantMessage(
            id="message-1",
            thread_id=assistant_thread.id,
            workspace_id=workspace.id,
            case_id=case.id,
            created_by_user_id=user.id,
            role="assistant",
            sequence=1,
            content_json={"text": "hello"},
            metadata_json={},
        ),
        AssistantCheckpoint(
            id="checkpoint-1",
            thread_id=assistant_thread.id,
            step_name="structured_response",
            state_json={"step": 1},
        ),
        Run(
            id="wf-1",
            scope_type=AssistantScope.case,
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            assistant_thread_id=assistant_thread.id,
            status=RunStatus.completed,
            run_type="workspace-check",
            thread_id="thread-key-1",
            provider_name="openai-compatible",
            model_name="model",
            result_json={},
        ),
    ])
    db_session.commit()

    case_dir = case_storage_dir(settings, workspace.id, case.id)
    scripts_dir = case_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "stdout.log").write_text("log", encoding="utf-8")
    workspace_analysis_dir = settings.outputs_dir / "workspaces" / workspace.id / "workspace-analyses" / "analysis-1"
    workspace_analysis_dir.mkdir(parents=True, exist_ok=True)
    (workspace_analysis_dir / "report.md").write_text("report", encoding="utf-8")

    counts = purge_workspace(db_session, settings, workspace)
    db_session.commit()

    assert counts.workspaces_deleted == 1
    assert counts.cases_deleted == 1
    assert db_session.get(Workspace, workspace.id) is None
    assert db_session.get(Case, case.id) is None
    assert db_session.query(WorkspaceMembership).count() == 0
    assert db_session.query(Artifact).count() == 0
    assert db_session.query(Run).count() == 0
    assert db_session.query(AssistantThread).count() == 0
    assert db_session.query(AssistantMessage).count() == 0
    assert db_session.query(AssistantCheckpoint).count() == 0
    assert db_session.query(Run).count() == 0
    assert not case_dir.exists()
    assert not (settings.outputs_dir / "workspaces" / workspace.id).exists()


def test_reset_sample_case_for_user_reseeds_clean_copy(db_session, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", settings.fs_data_root)
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", settings.outputs_dir)

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "001.mgz").write_bytes(b"t1")
    (sample_root / "aparc.DKTatlas+aseg.deep.mgz").write_bytes(b"seg")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    user = User(id="user-10", external_auth_id="user-10", email="user10@example.com", full_name="User Ten")
    workspace = Workspace(
        id="ws-10",
        owner_user_id=user.id,
        name="ws-10",
        kind="personal",
        is_default=True,
        status="active",
    )
    sample_case = Case(
        id=sample_seed_module.sample_case_id_for_workspace(workspace.id),
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title="broken-sample",
    )
    db_session.add_all([user, workspace, sample_case])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    old_relative_path = str((case_storage_dir(settings, workspace.id, sample_case.id) / "obsolete.txt").relative_to(settings.fs_data_root))
    old_artifact = Artifact(
        id="artifact-old",
        case_id=sample_case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.report,
        name="obsolete.txt",
        relative_path=old_relative_path,
        size_bytes=8,
        metadata_json={},
    )
    db_session.add(old_artifact)
    db_session.commit()

    old_case_dir = case_storage_dir(settings, workspace.id, sample_case.id)
    old_case_dir.mkdir(parents=True, exist_ok=True)
    (old_case_dir / "obsolete.txt").write_text("obsolete", encoding="utf-8")

    seeded = reset_sample_case_for_user(db_session, settings, user)
    db_session.commit()

    refreshed_case = db_session.get(Case, sample_seed_module.sample_case_id_for_workspace(workspace.id))
    refreshed_workspace = db_session.get(Workspace, workspace.id)
    new_case_dir = case_storage_dir(settings, refreshed_workspace.id, refreshed_case.id)

    assert seeded is True
    assert refreshed_case is not None
    assert refreshed_case.title == "sample-case"
    assert refreshed_workspace is not None
    assert refreshed_workspace.is_default is True
    assert (new_case_dir / "001.mgz").exists()
    assert (new_case_dir / "aparc.DKTatlas+aseg.deep.mgz").exists()
    assert not (new_case_dir / "obsolete.txt").exists()
    artifact_names = {
        artifact.name
        for artifact in db_session.query(Artifact).filter(Artifact.case_id == refreshed_case.id).all()
    }
    assert artifact_names == {"001.mgz", "aparc.DKTatlas+aseg.deep.mgz"}


def test_reset_sample_case_marks_mgz_outputs_as_volumes(db_session, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", settings.fs_data_root)
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", settings.outputs_dir)

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "orig.mgz").write_bytes(b"orig")
    (sample_root / "aparc.DKTatlas+aseg.deep.mgz").write_bytes(b"seg")
    (sample_root / "lh.pial").write_bytes(b"surface")
    (sample_root / "lh.curv").write_bytes(b"curvature")
    (sample_root / "lh.aparc.DKTatlas.mapped.annot").write_bytes(b"annotation")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    user = User(id="user-20", external_auth_id="user-20", email="user20@example.com", full_name="User Twenty")
    workspace = Workspace(
        id="ws-20",
        owner_user_id=user.id,
        name="ws-20",
        kind="personal",
        is_default=True,
        status="active",
    )
    sample_case = Case(
        id=sample_seed_module.sample_case_id_for_workspace(workspace.id),
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title="sample-case",
    )
    db_session.add_all([user, workspace, sample_case])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    db_session.add_all([
        Artifact(
            id="artifact-orig",
            case_id=sample_case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.report,
            name="orig.mgz",
            relative_path=str((case_storage_dir(settings, workspace.id, sample_case.id) / "orig.mgz").relative_to(settings.fs_data_root)),
            size_bytes=4,
            metadata_json={},
        ),
        Artifact(
            id="artifact-aparc",
            case_id=sample_case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.report,
            name="aparc.DKTatlas+aseg.deep.mgz",
            relative_path=str((case_storage_dir(settings, workspace.id, sample_case.id) / "aparc.DKTatlas+aseg.deep.mgz").relative_to(settings.fs_data_root)),
            size_bytes=3,
            metadata_json={},
        ),
    ])
    db_session.commit()

    seeded = reset_sample_case_for_user(db_session, settings, user)
    db_session.commit()

    artifacts = {
        artifact.name: artifact
        for artifact in db_session.query(Artifact).filter(Artifact.case_id == sample_case.id).all()
    }

    assert seeded is True
    assert artifacts["orig.mgz"].kind == ArtifactKind.volume
    assert artifacts["orig.mgz"].metadata_json["volume_role"] == "intensity"
    assert artifacts["aparc.DKTatlas+aseg.deep.mgz"].kind == ArtifactKind.volume
    assert artifacts["aparc.DKTatlas+aseg.deep.mgz"].metadata_json["volume_role"] == "segmentation"
    assert artifacts["aparc.DKTatlas+aseg.deep.mgz"].metadata_json["lut"] == "freesurfer"
    assert artifacts["lh.pial"].kind == ArtifactKind.derived
    assert artifacts["lh.pial"].metadata_json["layer_role"] == "surface"
    assert artifacts["lh.pial"].metadata_json["surface_name"] == "pial"
    assert artifacts["lh.pial"].metadata_json["visible"] is True
    assert artifacts["lh.curv"].kind == ArtifactKind.derived
    assert artifacts["lh.curv"].metadata_json["layer_role"] == "surface-curvature"
    assert artifacts["lh.aparc.DKTatlas.mapped.annot"].kind == ArtifactKind.derived
    assert artifacts["lh.aparc.DKTatlas.mapped.annot"].metadata_json["layer_role"] == "surface-annotation"


def test_ensure_sample_case_preserves_extra_generated_outputs(db_session, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", settings.fs_data_root)
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", settings.outputs_dir)

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "mri").mkdir()
    (sample_root / "mri" / "001.mgz").write_bytes(b"t1")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    user = User(id="user-extra", external_auth_id="user-extra", email="extra@example.com", full_name="Extra User")
    workspace = Workspace(
        id="ws-extra",
        owner_user_id=user.id,
        name="ws-extra",
        kind="personal",
        is_default=True,
        status="active",
    )
    db_session.add_all([user, workspace])
    db_session.commit()

    seeded_case = sample_seed_module.ensure_sample_case(db_session, user)
    db_session.commit()
    assert seeded_case is not None
    case_dir = case_storage_dir(settings, workspace.id, seeded_case.id)
    generated = case_dir / "mri" / "cc_mask.mgz"
    generated.write_bytes(b"generated")

    refreshed_case = sample_seed_module.ensure_sample_case(db_session, user)
    db_session.commit()
    assert refreshed_case is not None

    assert refreshed_case.id == seeded_case.id
    assert (case_dir / "mri" / "001.mgz").exists()
    assert generated.exists()


def test_ensure_sample_case_refresh_keeps_existing_artifact_ids(db_session, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", settings.fs_data_root)
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", settings.outputs_dir)

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "001.mgz").write_bytes(b"t1")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    user = User(id="user-40", external_auth_id="user-40", email="user40@example.com", full_name="User Forty")
    workspace = Workspace(
        id="ws-40",
        owner_user_id=user.id,
        name="ws-40",
        kind="personal",
        is_default=True,
        status="active",
    )
    sample_case = Case(
        id=sample_seed_module.sample_case_id_for_workspace(workspace.id),
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title="sample-case",
    )
    db_session.add_all([user, workspace, sample_case])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    db_session.add(
        Artifact(
            id="artifact-old-sample",
            case_id=sample_case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="001.mgz",
            relative_path=str((case_storage_dir(settings, workspace.id, sample_case.id) / "001.mgz").relative_to(settings.fs_data_root)),
            size_bytes=3,
            metadata_json={},
        )
    )
    db_session.add(
        AuditEvent(
            id="audit-old-sample",
            user_id=user.id,
            case_id=sample_case.id,
            artifact_id="artifact-old-sample",
            action="artifact.downloaded",
            details_json={},
        )
    )
    db_session.commit()

    refreshed_case = sample_seed_module.ensure_sample_case(db_session, user)
    db_session.commit()
    assert refreshed_case is not None

    audit_event = db_session.get(AuditEvent, "audit-old-sample")
    case_dir = case_storage_dir(settings, workspace.id, refreshed_case.id)
    artifact_names = {
        artifact.name
        for artifact in db_session.query(Artifact).filter(Artifact.case_id == refreshed_case.id).all()
    }

    assert refreshed_case is not None
    assert case_dir.exists()
    assert (case_dir / "001.mgz").exists()
    assert artifact_names == {"001.mgz"}
    assert audit_event is not None
    assert audit_event.artifact_id == "artifact-old-sample"
    refreshed_artifact = db_session.get(Artifact, "artifact-old-sample")
    assert refreshed_artifact is not None
    assert refreshed_artifact.name == "001.mgz"
    assert refreshed_artifact.size_bytes == 2
