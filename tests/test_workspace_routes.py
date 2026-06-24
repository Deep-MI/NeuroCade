"""Test workspace routes behavior for NeuroCade."""

import asyncio
import gzip
from io import BytesIO
from pathlib import Path
import sys
import zipfile

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.cases import operations as cases_module  # noqa: E402
from api_service.cases.identity import _replace_string_tokens  # noqa: E402
from api_service.cases.service import sync_analysis_run_status  # noqa: E402
from api_service.routers.cases import add_case_upload, create_case_with_upload, delete_case, queue_status, rename_case, save_generated_volume, start_run  # noqa: E402
from api_service.routers.workspaces import (  # noqa: E402
    cancel_batch_run,
    create_workspace,
    delete_workspace,
    get_workspace_batch_run,
    get_workspace_batch_runs,
    list_workspaces,
    update_workspace,
)
from api_service.workspace_batch import service as workspace_batch_module  # noqa: E402
from api_service.schemas import CaseRenameRequest, StartRunRequest, WorkspaceCreateRequest, WorkspaceDeleteRequest, WorkspaceUpdateRequest  # noqa: E402
from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import build_case_id, case_relative_prefix, case_storage_dir, workspace_storage_dir  # noqa: E402
from backend_common.db import AssistantMessage, AssistantScope, AssistantThread, Run, Artifact, ArtifactKind, AuditEvent, Base, Case, CaseEvent, RoleEnum, RunStatus, User, Workspace, WorkspaceMembership  # noqa: E402
from backend_common.runs import WORKSPACE_COMMAND_ACTION  # noqa: E402
from tests.factories import seed_workspace_context  # noqa: E402


def nifti_gzip_bytes() -> bytes:
    """Return a tiny gzipped NIfTI-like payload with a valid header magic."""
    header = bytearray(352)
    header[0:4] = (348).to_bytes(4, "little")
    header[344:348] = b"n+1\x00"
    return gzip.compress(bytes(header) + b"voxels")


def test_identity_rewrite_replaces_tokens_without_substring_damage():
    """ID rewrites should update embedded references without changing unrelated words."""
    updated = _replace_string_tokens(
        {
            "note": "Inspect lab__case-a at output/workspaces/lab/cases/case-a/mri/orig.mgz with collab notes",
            "thread": "workspace:lab",
            "unchanged": "collab-labyrinth",
        },
        {
            "lab": "renamed-lab",
            "lab__case-a": "renamed-lab__case-a",
            "output/workspaces/lab/cases/case-a": "output/workspaces/renamed-lab/cases/case-a",
        },
    )

    assert updated["note"] == (
        "Inspect renamed-lab__case-a at output/workspaces/renamed-lab/cases/case-a/mri/orig.mgz "
        "with collab notes"
    )
    assert updated["thread"] == "workspace:renamed-lab"
    assert updated["unchanged"] == "collab-labyrinth"


def test_case_id_columns_fit_max_workspace_and_case_slugs():
    """The confirmed 64/64 slug design must fit the canonical DB case ID."""
    def column_type_length(column: object) -> int:
        length = getattr(getattr(column, "type", None), "length", None)
        assert isinstance(length, int)
        return length

    workspace_slug = "w" * 64
    case_slug = "c" * 64
    case_id = build_case_id(workspace_slug, case_slug)

    assert len(case_id) == 130
    assert column_type_length(Case.__table__.c.id) >= len(case_id)
    case_fk_columns = [
        Artifact.__table__.c.case_id,
        AuditEvent.__table__.c.case_id,
        AssistantMessage.__table__.c.case_id,
        AssistantThread.__table__.c.case_id,
        CaseEvent.__table__.c.case_id,
        Run.__table__.c.case_id,
    ]
    assert all(column_type_length(column) >= len(case_id) for column in case_fk_columns)


@pytest.fixture()
def db_session():
    """Provide an isolated in-memory database session."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_context(db_session):
    """Create a default user, workspace, and auth context."""
    context, workspace, _cases = seed_workspace_context(
        db_session,
        workspace_id="workspace-default",
    )
    return db_session, context, workspace


def fk_db_session():
    """Provide a SQLite session with foreign-key checks enabled."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()


def seed_fk_workspace_context(db_session, *, workspace_id: str = "workspace-fk", case_slug: str = "case-a"):
    """Seed rows in explicit FK order for SQLite foreign-key enforcement."""
    user = User(id="user-fk", external_auth_id="user-fk", email="fk@example.com", full_name="FK User")
    db_session.add(user)
    db_session.commit()
    workspace = Workspace(
        id=workspace_id,
        owner_user_id=user.id,
        name=workspace_id,
        kind="personal",
        is_default=True,
        status="active",
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    case = Case(
        id=build_case_id(workspace.id, case_slug),
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title=case_slug,
    )
    db_session.add(case)
    db_session.commit()
    return AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"), workspace, case


def test_create_workspace_and_list_workspaces(seeded_context):
    db_session, context, workspace = seeded_context

    created = create_workspace(WorkspaceCreateRequest(name="study-a"), db=db_session, context=context)
    listed = list_workspaces(db=db_session, context=context)

    assert created.name == "study-a"
    assert {item.name for item in listed} == {"personal-workspace", "study-a"}


def test_rename_workspace(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("api_service.routers.workspaces.settings.fs_data_root", fs_root)
    monkeypatch.setattr("api_service.routers.workspaces.settings.outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.identity.settings.fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.identity.settings.outputs_dir_override", outputs_dir)

    renamed = update_workspace(workspace.id, WorkspaceUpdateRequest(name="my-workspace"), db=db_session, context=context)

    assert renamed.name == "my-workspace"


def test_rename_workspace_rewrites_workspace_parent_run_case_ids(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("api_service.routers.workspaces.settings.fs_data_root", fs_root)
    monkeypatch.setattr("api_service.routers.workspaces.settings.outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.identity.settings.fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.identity.settings.outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.routers.workspaces.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "case-a"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="case-a",
    )
    db_session.add(case)
    db_session.flush()
    old_workspace_dir = workspace_storage_dir(cases_module.settings, workspace.id)
    old_case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    old_case_dir.mkdir(parents=True, exist_ok=True)
    (old_case_dir / "case-a.mgz").write_bytes(b"scan")
    old_case_prefix = case_relative_prefix(workspace.id, case.id)
    db_session.add(
        Run(
            id="workspace-parent-run",
            workspace_id=workspace.id,
            case_id=None,
            created_by_user_id=context.user.id,
            scope_type=AssistantScope.workspace,
            status=RunStatus.completed,
            run_type=WORKSPACE_COMMAND_ACTION,
            thread_id=f"workspace:{workspace.id}",
            result_json={"case_ids": [case.id], "preview_path": f"{old_case_prefix}/case-a.mgz"},
        )
    )
    db_session.commit()

    renamed = update_workspace(workspace.id, WorkspaceUpdateRequest(name="renamed-workspace"), db=db_session, context=context)

    new_case_id = build_case_id("renamed-workspace", "case-a")
    new_case = db_session.get(Case, new_case_id)
    parent_run = db_session.get(Run, "workspace-parent-run")
    assert renamed.id == "renamed-workspace"
    assert new_case is not None
    assert parent_run is not None
    assert parent_run.workspace_id == "renamed-workspace"
    assert parent_run.thread_id == "workspace:renamed-workspace"
    assert parent_run.result_json["case_ids"] == [new_case_id]
    assert parent_run.result_json["preview_path"] == f"{case_relative_prefix('renamed-workspace', new_case_id)}/case-a.mgz"
    assert not old_workspace_dir.exists()
    assert case_storage_dir(cases_module.settings, "renamed-workspace", new_case_id).exists()


def test_rename_case_succeeds_with_foreign_keys_enabled(monkeypatch, tmp_path):
    db_session = fk_db_session()
    try:
        context, workspace, case = seed_fk_workspace_context(db_session)
        fs_root = tmp_path / "neurocade-data"
        outputs_dir = fs_root / "output"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
        monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
        monkeypatch.setattr("api_service.cases.identity.settings.fs_data_root", fs_root)
        monkeypatch.setattr("api_service.cases.identity.settings.outputs_dir_override", outputs_dir)

        old_case_prefix = case_relative_prefix(workspace.id, case.id)
        case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "case-a.mgz").write_bytes(b"scan")
        artifact = Artifact(
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="case-a.mgz",
            relative_path=f"{old_case_prefix}/case-a.mgz",
            size_bytes=4,
        )
        db_session.add(artifact)
        db_session.commit()

        rename_case(case.id, CaseRenameRequest(title="case-b"), db=db_session, context=context)

        new_case_id = build_case_id(workspace.id, "case-b")
        refreshed_artifact = db_session.get(Artifact, artifact.id)
        assert db_session.get(Case, new_case_id) is not None
        assert refreshed_artifact is not None
        assert refreshed_artifact.case_id == new_case_id
        assert refreshed_artifact.relative_path == f"{case_relative_prefix(workspace.id, new_case_id)}/case-a.mgz"
    finally:
        db_session.close()


def test_rename_workspace_succeeds_with_foreign_keys_enabled(monkeypatch, tmp_path):
    db_session = fk_db_session()
    try:
        context, workspace, case = seed_fk_workspace_context(db_session)
        fs_root = tmp_path / "neurocade-data"
        outputs_dir = fs_root / "output"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
        monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
        monkeypatch.setattr("api_service.routers.workspaces.settings.fs_data_root", fs_root)
        monkeypatch.setattr("api_service.routers.workspaces.settings.outputs_dir_override", outputs_dir)
        monkeypatch.setattr("api_service.cases.identity.settings.fs_data_root", fs_root)
        monkeypatch.setattr("api_service.cases.identity.settings.outputs_dir_override", outputs_dir)

        old_case_prefix = case_relative_prefix(workspace.id, case.id)
        workspace_storage_dir(cases_module.settings, workspace.id).mkdir(parents=True, exist_ok=True)
        case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "case-a.mgz").write_bytes(b"scan")
        artifact = Artifact(
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="case-a.mgz",
            relative_path=f"{old_case_prefix}/case-a.mgz",
            size_bytes=4,
        )
        db_session.add(artifact)
        db_session.commit()

        update_workspace(workspace.id, WorkspaceUpdateRequest(name="workspace-renamed"), db=db_session, context=context)

        new_case_id = build_case_id("workspace-renamed", "case-a")
        refreshed_artifact = db_session.get(Artifact, artifact.id)
        refreshed_membership = db_session.query(WorkspaceMembership).filter_by(user_id=context.user.id).one()
        assert db_session.get(Workspace, "workspace-renamed") is not None
        assert db_session.get(Case, new_case_id) is not None
        assert refreshed_membership.workspace_id == "workspace-renamed"
        assert refreshed_artifact is not None
        assert refreshed_artifact.workspace_id == "workspace-renamed"
        assert refreshed_artifact.case_id == new_case_id
        assert refreshed_artifact.relative_path == f"{case_relative_prefix('workspace-renamed', new_case_id)}/case-a.mgz"
    finally:
        db_session.close()


def test_rename_workspace_rejects_active_case(seeded_context):
    db_session, context, workspace = seeded_context
    case = Case(
        id=build_case_id(workspace.id, "active-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="active-case",
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(
        Run(
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.running,
            run_type="run_fastsurfer",
            runtime_job_id=case.id,
            result_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        update_workspace(workspace.id, WorkspaceUpdateRequest(name="renamed-workspace"), db=db_session, context=context)

    assert exc_info.value.status_code == 409


def test_rename_workspace_rejects_active_workspace_run(seeded_context):
    db_session, context, workspace = seeded_context
    db_session.add(
        Run(
            id="workspace-run-active",
            workspace_id=workspace.id,
            case_id=None,
            created_by_user_id=context.user.id,
            scope_type=AssistantScope.workspace,
            status=RunStatus.running,
            run_type="workspace_bash",
            result_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        update_workspace(workspace.id, WorkspaceUpdateRequest(name="renamed-workspace"), db=db_session, context=context)

    assert exc_info.value.status_code == 409
    assert db_session.get(Workspace, workspace.id) is not None


def test_delete_workspace_with_cases_requires_confirmation(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("api_service.routers.workspaces.settings.fs_data_root", fs_root)
    monkeypatch.setattr("api_service.routers.workspaces.settings.outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.routers.workspaces.log_event", lambda *args, **kwargs: None)

    extra_workspace = Workspace(
        id="workspace-extra",
        owner_user_id=context.user.id,
        name="Shared",
        kind="shared",
        is_default=False,
        status="active",
    )
    db_session.add(extra_workspace)
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=extra_workspace.id, user_id=context.user.id, role=RoleEnum.owner, granted_by_user_id=context.user.id))
    db_session.add(
        Case(
            id=build_case_id(extra_workspace.id, "case"),
            workspace_id=extra_workspace.id,
            owner_user_id=context.user.id,
            title="case",
        )
    )
    workspace_dir = workspace_storage_dir(cases_module.settings, extra_workspace.id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        delete_workspace(
            extra_workspace.id,
            WorkspaceDeleteRequest(confirm_non_empty_delete=False),
            db=db_session,
            context=context,
        )

    assert exc_info.value.status_code == 409
    deleted = delete_workspace(
        extra_workspace.id,
        WorkspaceDeleteRequest(confirm_non_empty_delete=True),
        db=db_session,
        context=context,
    )

    assert deleted == {"deleted": extra_workspace.id}
    assert db_session.get(Workspace, extra_workspace.id) is None
    assert db_session.query(Case).filter(Case.workspace_id == extra_workspace.id).count() == 0
    assert not workspace_dir.exists()


def test_create_case_with_upload_uses_explicit_title(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    upload = UploadFile(filename="scan.nii.gz", file=BytesIO(gzip.compress(b"test-bytes")))
    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="chosen-name",
            file=upload,
            db=db_session,
            context=context,
        )
    )

    created_case = db_session.query(Case).filter(Case.id == response.case_id).one()
    upload_artifact = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == created_case.id, Artifact.kind == ArtifactKind.volume)
        .one()
    )
    upload_event = (
        db_session.query(CaseEvent)
        .filter(CaseEvent.case_id == created_case.id, CaseEvent.event_type == "case.uploaded")
        .one()
    )
    assert created_case.id == build_case_id(workspace.id, "chosen-name")
    assert created_case.title == "chosen-name"
    assert response.title == "chosen-name"
    assert response.filename == "chosen-name.nii.gz"
    assert upload_artifact.metadata_json["volume_role"] == "intensity"
    assert upload_event.artifact_id == upload_artifact.id
    assert upload_event.details_json["filename"] == "chosen-name.nii.gz"
    assert db_session.query(Run).filter(Run.case_id == created_case.id).count() == 0
    stored_path = fs_root / upload_artifact.relative_path
    assert gzip.decompress(stored_path.read_bytes()) == b"test-bytes"


def test_create_case_with_valid_gzipped_nifti_upload(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    upload = UploadFile(filename="scan.nii.gz", file=BytesIO(nifti_gzip_bytes()))
    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="nifti-case",
            file=upload,
            db=db_session,
            context=context,
        )
    )

    artifact = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == response.case_id, Artifact.kind == ArtifactKind.volume)
        .one()
    )

    assert response.case_id == build_case_id(workspace.id, "nifti-case")
    assert response.filename == "nifti-case.nii.gz"
    assert artifact.name == "nifti-case.nii.gz"
    assert (fs_root / artifact.relative_path).exists()


def test_save_generated_volume_registers_segmentation_artifact_with_collision_safe_name(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "drawing-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="drawing-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "annotation.nii").write_bytes(b"existing")
    db_session.commit()

    artifact = asyncio.run(
        save_generated_volume(
            case_id=case.id,
            filename="annotation.nii",
            metadata='{"layer_role": "drawing", "source_layer_id": "drawing-1", "lut": "binary"}',
            file=UploadFile(filename="annotation.nii", file=BytesIO(b"nifti-bytes")),
            db=db_session,
            context=context,
        )
    )

    assert artifact.name == "annotation-2.nii"
    assert artifact.kind == "volume"
    assert artifact.metadata["volume_role"] == "segmentation"
    assert artifact.metadata["lut"] == "binary"
    assert artifact.metadata["layer_role"] == "drawing"
    assert artifact.metadata["source_layer_id"] == "drawing-1"
    assert (case_dir / "annotation-2.nii").read_bytes() == b"nifti-bytes"

    row = db_session.query(Artifact).filter(Artifact.id == artifact.id).one()
    assert row.relative_path == f"{case_relative_prefix(workspace.id, case.id)}/annotation-2.nii"
    assert row.size_bytes == len(b"nifti-bytes")


def test_failed_case_upload_does_not_reserve_case_title(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            create_case_with_upload(
                workspace_id=workspace.id,
                title="retry-case",
                file=UploadFile(filename="scan.nii.gz", file=BytesIO(b"not-gzip")),
                db=db_session,
                context=context,
            )
        )

    assert exc_info.value.status_code == 400
    assert db_session.query(Case).filter(Case.workspace_id == workspace.id, Case.title == "retry-case").count() == 0

    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="retry-case",
            file=UploadFile(filename="scan.nii.gz", file=BytesIO(nifti_gzip_bytes())),
            db=db_session,
            context=context,
        )
    )

    assert response.title == "retry-case"


def test_add_case_upload_skips_missing_artifact_filename_collision(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)

    case = Case(
        id=build_case_id(workspace.id, "stale-artifact-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="stale-artifact-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    stale_rel = f"{case_relative_prefix(workspace.id, case.id)}/scan.mgz"
    db_session.add(
        Artifact(
            id="artifact-stale-scan",
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="scan.mgz",
            relative_path=stale_rel,
            mime_type="application/octet-stream",
            size_bytes=10,
            metadata_json={"volume_role": "intensity"},
        )
    )
    db_session.commit()
    assert not (fs_root / stale_rel).exists()

    response = asyncio.run(
        add_case_upload(
            case_id=case.id,
            file=UploadFile(filename="scan.mgz", file=BytesIO(gzip.compress(b"new-mgz"))),
            db=db_session,
            context=context,
        )
    )

    assert response.filename == "scan-2.mgz"
    assert not (fs_root / stale_rel).exists()
    assert (case_dir / "scan-2.mgz").exists()


def test_create_case_with_dicom_upload_converts_all_outputs_and_marks_structural_input_candidate(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr(cases_module.settings, "dicom_raw_retention", "discard")
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    def fake_dcm2niix(_input_dir, output_dir):
        (output_dir / "localizer_1.nii.gz").write_bytes(b"localizer")
        (output_dir / "localizer_1.json").write_text('{"SeriesDescription": "Localizer"}', encoding="utf-8")
        (output_dir / "mprage_2.nii.gz").write_bytes(b"structural-volume")
        (output_dir / "mprage_2.json").write_text('{"SeriesDescription": "MPRAGE T1w"}', encoding="utf-8")

    monkeypatch.setattr("api_service.cases.uploads._run_dcm2niix", fake_dcm2niix)

    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="dicom-case",
            files=[
                UploadFile(filename="IM-0001.dcm", file=BytesIO(b"dicom-1")),
                UploadFile(filename="IM-0002.dcm", file=BytesIO(b"dicom-2")),
            ],
            db=db_session,
            context=context,
        )
    )

    created_case = db_session.query(Case).filter(Case.id == response.case_id).one()
    artifacts = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == created_case.id, Artifact.kind == ArtifactKind.volume)
        .order_by(Artifact.name.asc())
        .all()
    )
    selected_input = next(artifact for artifact in artifacts if artifact.metadata_json.get("dicom_selected_input_candidate") is True)

    assert response.filename == "dicom-case.nii.gz"
    assert len(artifacts) == 2
    assert selected_input.name == "dicom-case.nii.gz"
    assert selected_input.metadata_json["dicom_converted"] is True
    assert selected_input.metadata_json["dicom_input_selection_reason"] == "structural series hint"
    assert (fs_root / selected_input.relative_path).read_bytes() == b"structural-volume"
    assert all((fs_root / artifact.relative_path).exists() for artifact in artifacts)


def test_dicom_upload_can_archive_raw_sources_when_configured(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr(cases_module.settings, "dicom_raw_retention", "archive")
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    def fake_dcm2niix(_input_dir, output_dir):
        (output_dir / "t1_1.nii.gz").write_bytes(b"converted")
        (output_dir / "t1_1.json").write_text('{"SeriesDescription": "T1w"}', encoding="utf-8")

    monkeypatch.setattr("api_service.cases.uploads._run_dcm2niix", fake_dcm2niix)

    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="raw-archive-case",
            file=UploadFile(filename="IM-0001.dcm", file=BytesIO(b"dicom")),
            db=db_session,
            context=context,
        )
    )

    raw_artifact = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == response.case_id, Artifact.kind == ArtifactKind.derived)
        .one()
    )
    assert raw_artifact.name == "raw-dicom.zip"
    assert raw_artifact.metadata_json["dicom_raw"] is True
    assert (fs_root / raw_artifact.relative_path).exists()


def test_dicom_zip_upload_is_extracted_before_conversion(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr(cases_module.settings, "dicom_raw_retention", "discard")
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("series/IM-0001.dcm", b"dicom")
    zip_buffer.seek(0)

    def fake_dcm2niix(input_dir, output_dir):
        assert (input_dir / "series" / "series" / "IM-0001.dcm").exists()
        (output_dir / "t1_1.nii.gz").write_bytes(b"converted")
        (output_dir / "t1_1.json").write_text('{"SeriesDescription": "T1w"}', encoding="utf-8")

    monkeypatch.setattr("api_service.cases.uploads._run_dcm2niix", fake_dcm2niix)

    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="zip-dicom-case",
            file=UploadFile(filename="series.zip", file=zip_buffer),
            db=db_session,
            context=context,
        )
    )

    assert response.filename == "zip-dicom-case.nii.gz"


def test_deleted_case_title_can_be_reused(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "reusable-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="reusable-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "reusable-case.mgz").write_bytes(b"old")
    db_session.commit()

    asyncio.run(delete_case(case.id, db=db_session, context=context))
    upload = UploadFile(filename="scan.mgz", file=BytesIO(gzip.compress(b"new")))
    response = asyncio.run(
        create_case_with_upload(
            workspace_id=workspace.id,
            title="reusable-case",
            file=upload,
            db=db_session,
            context=context,
        )
    )

    matching_cases = (
        db_session.query(Case)
        .filter(Case.workspace_id == workspace.id, Case.title == "reusable-case")
        .all()
    )
    assert response.case_id == build_case_id(workspace.id, "reusable-case")
    assert len(matching_cases) == 1


def test_add_case_upload_preserves_existing_case_outputs(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "existing-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()
    case_upload_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_upload_dir.mkdir(parents=True, exist_ok=True)
    original_upload = case_upload_dir / "existing-case.mgz"
    original_upload.write_bytes(b"old-bytes")
    original_artifact = Artifact(
        id="artifact-original-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="existing-case.mgz",
        relative_path=str(original_upload.resolve().relative_to(fs_root.resolve())),
        mime_type="application/octet-stream",
        size_bytes=len(b"old-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(original_artifact)
    db_session.flush()
    old_volume_path = case_upload_dir / "mri" / "old-seg.mgz"
    old_volume_path.parent.mkdir(parents=True, exist_ok=True)
    old_volume_path.write_bytes(b"old-seg")
    old_log_path = case_upload_dir / "scripts" / "stdout.log"
    old_log_path.parent.mkdir(parents=True, exist_ok=True)
    old_log_path.write_text("stale logs", encoding="utf-8")
    case_prefix = case_relative_prefix(workspace.id, case.id)
    db_session.add_all(
        [
            Artifact(
                id="artifact-old-volume",
                case_id=case.id,
                workspace_id=workspace.id,
                kind=ArtifactKind.volume,
                name="old-seg.mgz",
                relative_path=f"{case_prefix}/mri/old-seg.mgz",
                mime_type="application/octet-stream",
                size_bytes=len(b"old-seg"),
                metadata_json={"volume_role": "segmentation"},
            ),
            Artifact(
                id="artifact-old-log",
                case_id=case.id,
                workspace_id=workspace.id,
                kind=ArtifactKind.log,
                name="stdout.log",
                relative_path=f"{case_prefix}/scripts/stdout.log",
                mime_type="text/plain",
                size_bytes=len("stale logs"),
                metadata_json={},
            ),
        ]
    )
    db_session.add(
        AuditEvent(
            id="audit-old-volume",
            user_id=context.user.id,
            case_id=case.id,
            artifact_id="artifact-old-volume",
            action="artifact.downloaded",
            details_json={},
        )
    )
    db_session.add(
        Run(
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.completed,
            run_type="run_fastsurfer",
            runtime_job_id=case.id,
            result_json={},
        )
    )
    db_session.commit()

    upload = UploadFile(filename="replacement.nii.gz", file=BytesIO(gzip.compress(b"new-bytes")))
    response = asyncio.run(
        add_case_upload(
            case_id=case.id,
            file=upload,
            db=db_session,
            context=context,
        )
    )

    case_upload_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    upload_artifacts = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == case.id, Artifact.kind == ArtifactKind.volume)
        .order_by(Artifact.created_at.asc())
        .all()
    )
    input_artifacts = [
        artifact
        for artifact in upload_artifacts
        if (artifact.metadata_json or {}).get("volume_role") == "intensity"
    ]
    all_artifact_names = {
        artifact.name
        for artifact in db_session.query(Artifact).filter(Artifact.case_id == case.id).all()
    }
    upload_event = db_session.query(CaseEvent).filter(CaseEvent.case_id == case.id, CaseEvent.event_type == "case.uploaded").one_or_none()
    audit_event = db_session.get(AuditEvent, "audit-old-volume")

    assert db_session.query(Case).count() == 1
    assert response.case_id == case.id
    assert response.title == case.title
    assert response.filename == "replacement.nii.gz"
    assert [artifact.name for artifact in input_artifacts] == ["existing-case.mgz", "replacement.nii.gz"]
    assert (case_upload_dir / "existing-case.mgz").read_bytes() == b"old-bytes"
    assert gzip.decompress((case_upload_dir / "replacement.nii.gz").read_bytes()) == b"new-bytes"
    assert old_volume_path.exists()
    assert old_log_path.exists()
    assert "old-seg.mgz" in all_artifact_names
    assert "stdout.log" in all_artifact_names
    assert upload_event is not None
    assert upload_event.artifact_id is not None
    assert db_session.query(Run).filter(Run.case_id == case.id).count() == 1
    assert audit_event is not None
    assert audit_event.artifact_id == "artifact-old-volume"


def test_start_run_persists_queued_run_before_runtime_handoff(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "run-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="run-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    upload_path = case_dir / "run-case.mgz"
    upload_path.write_bytes(b"scan-bytes")
    artifact = Artifact(
        id="artifact-run-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="run-case.mgz",
        relative_path=str(upload_path.resolve().relative_to(fs_root.resolve())),
        mime_type="application/octet-stream",
        size_bytes=len(b"scan-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.commit()

    async def fake_start_run(payload):
        queued_run = (
            db_session.query(Run)
            .filter(Run.case_id == case.id, Run.status == RunStatus.queued)
            .one_or_none()
        )
        assert queued_run is not None
        assert queued_run.external_task_id is None
        return {"case_id": payload["case_id"], "task_id": "task-1", "status": "queued"}

    monkeypatch.setattr(cases_module.runtime_service, "start_run", fake_start_run)

    response = asyncio.run(
        start_run(
            StartRunRequest(
                workspace_id=workspace.id,
                case_id=case.id,
                source_case_id=None,
                input_artifact_id=artifact.id,
                seg_only=True,
                surf_only=False,
                no_bias=False,
                no_cereb=False,
                no_asegdkt=False,
                no_hypothal=False,
                three_t=False,
                vox_size="min",
            ),
            db=db_session,
            context=context,
        )
    )

    run = db_session.get(Run, response.id)
    assert run is not None
    assert run.external_task_id == "task-1"
    assert run.result_json["status"] == "queued"


def test_start_run_active_constraint_returns_conflict_when_precheck_races(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr(cases_module, "ensure_case_not_active", lambda *_args, **_kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "active-conflict"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="active-conflict",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    upload_path = case_dir / "active-conflict.mgz"
    upload_path.write_bytes(b"scan-bytes")
    artifact = Artifact(
        id="artifact-active-conflict-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="active-conflict.mgz",
        relative_path=str(upload_path.resolve().relative_to(fs_root.resolve())),
        mime_type="application/octet-stream",
        size_bytes=len(b"scan-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.add(
        Run(
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.queued,
            run_type="run_fastsurfer",
            runtime_job_id=case.id,
            result_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            start_run(
                StartRunRequest(
                    workspace_id=workspace.id,
                    case_id=case.id,
                    source_case_id=None,
                    input_artifact_id=artifact.id,
                    seg_only=True,
                    surf_only=False,
                    no_bias=False,
                    no_cereb=False,
                    no_asegdkt=False,
                    no_hypothal=False,
                    three_t=False,
                    vox_size="min",
                ),
                db=db_session,
                context=context,
            )
        )

    assert exc_info.value.status_code == 409


def test_sync_analysis_run_status_skips_terminal_runs(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context
    case = Case(
        id=build_case_id(workspace.id, "completed-sync"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="completed-sync",
    )
    db_session.add(case)
    db_session.flush()
    run = Run(
        case_id=case.id,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        status=RunStatus.completed,
        run_type="run_fastsurfer",
        runtime_job_id=case.id,
        result_json={},
    )
    db_session.add(run)
    db_session.commit()

    async def fake_fetch_status(_case_id: str, _workspace_id: str):
        raise AssertionError("terminal runs should not call runtime status")

    monkeypatch.setattr(cases_module.runtime_service, "fetch_status", fake_fetch_status)

    refreshed = asyncio.run(sync_analysis_run_status(case, run, db_session))

    assert refreshed is not None
    assert refreshed.status == RunStatus.completed


def test_sync_analysis_run_status_does_not_overwrite_canceled_run(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context
    case = Case(
        id=build_case_id(workspace.id, "cancel-sync"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="cancel-sync",
    )
    db_session.add(case)
    db_session.flush()
    run = Run(
        case_id=case.id,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        status=RunStatus.queued,
        run_type="run_fastsurfer",
        runtime_job_id=case.id,
        result_json={},
    )
    db_session.add(run)
    db_session.commit()

    async def fake_fetch_status(_case_id: str, _workspace_id: str):
        run.status = RunStatus.canceled
        db_session.commit()
        return {"status": "running"}

    monkeypatch.setattr(cases_module.runtime_service, "fetch_status", fake_fetch_status)

    refreshed = asyncio.run(sync_analysis_run_status(case, run, db_session))

    assert refreshed is not None
    assert refreshed.status == RunStatus.canceled


def test_add_case_upload_uses_unique_filename_for_duplicates(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "existing-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()
    case_upload_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_upload_dir.mkdir(parents=True, exist_ok=True)
    original_upload = case_upload_dir / "replacement.nii.gz"
    original_upload.write_bytes(b"old-bytes")
    original_artifact = Artifact(
        id="artifact-original-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="replacement.nii.gz",
        relative_path=str(original_upload.resolve().relative_to(fs_root.resolve())),
        mime_type="application/octet-stream",
        size_bytes=len(b"old-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(original_artifact)
    db_session.flush()
    db_session.commit()

    upload = UploadFile(filename="replacement.nii.gz", file=BytesIO(gzip.compress(b"new-bytes")))
    response = asyncio.run(
        add_case_upload(
            case_id=case.id,
            file=upload,
            db=db_session,
            context=context,
        )
    )

    case_upload_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    upload_artifact = (
        db_session.query(Artifact)
        .filter(Artifact.case_id == case.id, Artifact.kind == ArtifactKind.volume)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    upload_artifact_names = {
        artifact.name
        for artifact in db_session.query(Artifact)
        .filter(Artifact.case_id == case.id, Artifact.kind == ArtifactKind.volume)
        .all()
    }

    assert response.filename == "replacement-2.nii.gz"
    assert upload_artifact is not None
    assert upload_artifact_names == {"replacement.nii.gz", "replacement-2.nii.gz"}
    assert gzip.decompress((case_upload_dir / "replacement-2.nii.gz").read_bytes()) == b"new-bytes"


def test_add_case_upload_rejects_existing_case_when_case_is_active(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)

    case = Case(
        id=build_case_id(workspace.id, "running-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="running-case",
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(
        Run(
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.running,
            run_type="run_fastsurfer",
            runtime_job_id=case.id,
            result_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            add_case_upload(
                case_id=case.id,
                file=UploadFile(filename="replacement.nii.gz", file=BytesIO(gzip.compress(b"new-bytes"))),
                db=db_session,
                context=context,
            )
        )

    assert exc_info.value.status_code == 409


def test_rename_case_updates_case_title_without_renaming_input_volume(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "existing-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()

    old_case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    old_case_dir.mkdir(parents=True, exist_ok=True)
    original_upload = old_case_dir / "existing-case.mgz"
    original_upload.write_bytes(b"scan-bytes")
    artifact = Artifact(
        id="artifact-input-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="existing-case.mgz",
        relative_path=str(original_upload.resolve().relative_to(fs_root.resolve())),
        mime_type="application/octet-stream",
        size_bytes=len(b"scan-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.commit()

    response = rename_case(
        case.id,
        CaseRenameRequest(title="renamed-case"),
        db=db_session,
        context=context,
    )

    old_case_id = build_case_id(workspace.id, "existing-case")
    new_case_id = build_case_id(workspace.id, "renamed-case")
    case = db_session.get(Case, new_case_id)
    assert case is not None
    db_session.refresh(artifact)
    renamed_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)

    response_payload = response.model_dump()
    assert response_payload["case_id"] == new_case_id
    assert response_payload["old_id"] == old_case_id
    assert response_payload["new_id"] == new_case_id
    assert response_payload["old_title"] == "existing-case"
    assert response_payload["new_title"] == "renamed-case"
    assert response_payload["title"] == "renamed-case"
    assert case.title == "renamed-case"
    assert not old_case_dir.exists()
    assert renamed_dir.exists()
    assert (renamed_dir / "existing-case.mgz").read_bytes() == b"scan-bytes"
    assert artifact.case_id == new_case_id
    assert artifact.name == "existing-case.mgz"
    assert artifact.relative_path == f"{case_relative_prefix(workspace.id, case.id)}/existing-case.mgz"


def test_rename_case_rewrites_workspace_scoped_references(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "existing-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()

    old_case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    old_case_dir.mkdir(parents=True, exist_ok=True)
    (old_case_dir / "existing-case.mgz").write_bytes(b"scan-bytes")
    old_case_prefix = case_relative_prefix(workspace.id, case.id)
    workspace_thread = AssistantThread(
        id="workspace-thread",
        thread_key=f"workspace:{workspace.id}",
        scope_type=AssistantScope.workspace,
        workspace_id=workspace.id,
        case_id=None,
        created_by_user_id=context.user.id,
        provider_name="openai-compatible",
        model_name="qwen",
    )
    db_session.add(workspace_thread)
    db_session.add(
        AssistantMessage(
            id="workspace-message",
            thread_id=workspace_thread.id,
            workspace_id=workspace.id,
            case_id=None,
            created_by_user_id=context.user.id,
            role="assistant",
            sequence=1,
            content_json={"value": f"Inspect {case.id} at {old_case_prefix}/existing-case.mgz"},
            metadata_json={"selected_case_id": case.id},
        )
    )
    db_session.add(
        Artifact(
            id="workspace-artifact",
            case_id=None,
            workspace_id=workspace.id,
            kind=ArtifactKind.report,
            name="cases.json",
            relative_path=f"output/workspaces/{workspace.id}/workspace-analyses/ws-analysis/cases.json",
            mime_type="application/json",
            size_bytes=2,
            metadata_json={"selected_case_ids": [case.id]},
        )
    )
    db_session.add(
        Run(
            id="workspace-parent-run",
            workspace_id=workspace.id,
            case_id=None,
            created_by_user_id=context.user.id,
            scope_type=AssistantScope.workspace,
            status=RunStatus.completed,
            run_type=WORKSPACE_COMMAND_ACTION,
            thread_id=f"workspace:{workspace.id}",
            input_json={"selected_case_id": case.id},
            result_json={"case_ids": [case.id], "preview_path": f"{old_case_prefix}/existing-case.mgz"},
        )
    )
    db_session.commit()

    rename_case(case.id, CaseRenameRequest(title="renamed-case"), db=db_session, context=context)

    new_case_id = build_case_id(workspace.id, "renamed-case")
    parent_run = db_session.get(Run, "workspace-parent-run")
    workspace_message = db_session.get(AssistantMessage, "workspace-message")
    workspace_artifact = db_session.get(Artifact, "workspace-artifact")
    assert parent_run is not None
    assert parent_run.input_json["selected_case_id"] == new_case_id
    assert parent_run.result_json["case_ids"] == [new_case_id]
    assert parent_run.result_json["preview_path"] == f"{case_relative_prefix(workspace.id, new_case_id)}/existing-case.mgz"
    assert workspace_message is not None
    assert workspace_message.content_json["value"] == f"Inspect {new_case_id} at {case_relative_prefix(workspace.id, new_case_id)}/existing-case.mgz"
    assert workspace_message.metadata_json["selected_case_id"] == new_case_id
    assert workspace_artifact is not None
    assert workspace_artifact.metadata_json["selected_case_ids"] == [new_case_id]


def test_rename_case_rejects_active_workspace_command(seeded_context):
    db_session, context, workspace = seeded_context
    case = Case(
        id=build_case_id(workspace.id, "selected-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="selected-case",
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(
        Run(
            id="active-workspace-command",
            workspace_id=workspace.id,
            case_id=None,
            created_by_user_id=context.user.id,
            scope_type=AssistantScope.workspace,
            status=RunStatus.running,
            run_type=WORKSPACE_COMMAND_ACTION,
            result_json={"case_ids": [case.id]},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        rename_case(case.id, CaseRenameRequest(title="renamed-selected-case"), db=db_session, context=context)

    assert exc_info.value.status_code == 409


def test_rename_case_rolls_back_title_when_storage_update_fails(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)

    case = Case(
        id=build_case_id(workspace.id, "stable-case"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="stable-case",
    )
    db_session.add(case)
    db_session.commit()
    old_case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    new_case_id = build_case_id(workspace.id, "broken-rename")
    new_case_dir = case_storage_dir(cases_module.settings, workspace.id, new_case_id)

    def fail_storage_layout(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr("api_service.cases.operations.ensure_case_storage_layout", fail_storage_layout)

    with pytest.raises(OSError, match="storage unavailable"):
        rename_case(
            case.id,
            CaseRenameRequest(title="broken-rename"),
            db=db_session,
            context=context,
        )

    db_session.refresh(case)
    assert case.title == "stable-case"
    assert not old_case_dir.exists()
    assert not new_case_dir.exists()


def test_delete_case_removes_db_row_and_storage(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(cases_module.settings, "outputs_dir_override", outputs_dir)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id=build_case_id(workspace.id, "delete-me"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="delete-me",
    )
    db_session.add(case)
    db_session.flush()

    case_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)
    case_dir.mkdir(parents=True, exist_ok=True)
    upload_path = case_dir / "delete-me.mgz"
    upload_path.write_bytes(b"scan-bytes")
    db_session.commit()

    response = asyncio.run(delete_case(case.id, db=db_session, context=context))

    assert response == {"deleted": case.id}
    assert db_session.get(Case, case.id) is None
    assert not case_dir.exists()


def test_queue_status_requires_workspace_manager_role(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context
    membership = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id, WorkspaceMembership.user_id == context.user.id)
        .one()
    )
    membership.role = RoleEnum.user
    db_session.commit()

    async def fake_fetch_queue_status():
        return {"active": 1, "queued": 2, "total": 3}

    monkeypatch.setattr("api_service.routers.cases.runtime_service.fetch_queue_status", fake_fetch_queue_status)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(queue_status(workspace_id=workspace.id, db=db_session, context=context))

    assert exc_info.value.status_code == 403


def test_queue_status_returns_counts_for_workspace_owner(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context

    async def fake_fetch_queue_status():
        return {"active": 1, "queued": 2, "total": 3}

    monkeypatch.setattr("api_service.routers.cases.runtime_service.fetch_queue_status", fake_fetch_queue_status)

    result = asyncio.run(queue_status(workspace_id=workspace.id, db=db_session, context=context))

    assert result == {"active": 1, "queued": 2, "total": 3}


def test_workspace_batch_routes_return_and_cancel_batch_run(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    case_a = Case(
        id=build_case_id(workspace.id, "batch-a"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="batch-a",
    )
    case_b = Case(
        id=build_case_id(workspace.id, "batch-b"),
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="batch-b",
    )
    db_session.add_all([case_a, case_b])
    db_session.commit()

    monkeypatch.setattr(workspace_batch_module.settings, "fs_data_root", tmp_path / "neurocade-data")
    monkeypatch.setattr(
        workspace_batch_module,
        "queue_workspace_batch_case",
        lambda run_id, case_id, *, is_probe: f"task-{case_id}",
    )

    summary = workspace_batch_module.create_workspace_batch_run(
        db_session,
        context,
        workspace,
        command="mri_synthstrip --help | head",
        report_name="batch-route-test",
        case_ids=[case_a.id, case_b.id],
        thread_id="workspace:workspace-default",
        provider_name="openai-compatible",
        model_name="qwen",
    )

    listed = get_workspace_batch_runs(workspace.id, db=db_session, context=context)
    detail = get_workspace_batch_run(workspace.id, summary.run_id, db=db_session, context=context)

    from api_service.jobs import job_manager

    monkeypatch.setattr(job_manager, "cancel", lambda task_id: True)
    canceled = cancel_batch_run(workspace.id, summary.run_id, db=db_session, context=context)

    assert listed[0].run_id == summary.run_id
    assert detail.total_cases == 2
    assert canceled.status == "canceled"
