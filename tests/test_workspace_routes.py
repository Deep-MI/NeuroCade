"""Test workspace routes behavior for NeuroCade."""

import asyncio
import gzip
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.cases import operations as cases_module  # noqa: E402
from api_service.cases import run_operations as run_operations_module  # noqa: E402
from api_service.cases import uploads as uploads_module  # noqa: E402
from api_service.helpers import get_case_for_user  # noqa: E402
from api_service.routers.cases import (  # noqa: E402
    add_case_upload,
    create_case_with_upload,
    delete_case,
    queue_status,
    save_generated_volume,
    start_run,
    update_case,
)
from api_service.routers.workspaces import (  # noqa: E402
    create_workspace,
    delete_workspace,
    list_workspaces,
    update_workspace,
)
from api_service.runtime import workflow_runs as workflow_runs_module  # noqa: E402
from api_service.schemas import (  # noqa: E402
    CaseUpdateRequest,
    StartRunRequest,
    WorkspaceCreateRequest,
    WorkspaceDeleteRequest,
    WorkspaceUpdateRequest,
)

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import (  # noqa: E402
    case_storage_dir,
    ensure_case_storage_layout,
    ensure_workspace_storage_layout,
    workspace_storage_dir,
)
from backend_common.db import (  # noqa: E402
    Artifact,
    ArtifactKind,
    AssistantMessage,
    AssistantScope,
    AssistantThread,
    AuditEvent,
    Base,
    Case,
    CaseEvent,
    RoleEnum,
    Run,
    RunStatus,
    User,
    Workspace,
    WorkspaceMembership,
)
from backend_common.storage import resolve_artifact_path  # noqa: E402
from tests.factories import seed_workspace_context  # noqa: E402


def nifti_gzip_bytes() -> bytes:
    """Return a tiny gzipped NIfTI-like payload with a valid header magic."""
    header = bytearray(352)
    header[0:4] = (348).to_bytes(4, "little")
    header[344:348] = b"n+1\x00"
    return gzip.compress(bytes(header) + b"voxels")


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
def seeded_context(db_session, monkeypatch, tmp_path):
    """Create a default user, workspace, and auth context."""
    context, workspace, _cases = seed_workspace_context(
        db_session,
        workspace_id="workspace-default",
    )
    monkeypatch.setattr(cases_module.settings, "fs_data_root", tmp_path / "neurocade-data")
    ensure_workspace_storage_layout(cases_module.settings, workspace)
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
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    case = Case(
        id=f"{case_slug}-id",
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


def test_rename_workspace(seeded_context):
    db_session, context, workspace = seeded_context

    renamed = update_workspace(workspace.id, WorkspaceUpdateRequest(name="my-workspace"), db=db_session, context=context)

    assert renamed.name == "my-workspace"


def test_rename_workspace_keeps_ids_and_references_stable(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context
    monkeypatch.setattr("api_service.routers.workspaces.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="case-a-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="case-a",
    )
    db_session.add(case)
    db_session.flush()
    old_workspace_dir = workspace_storage_dir(cases_module.settings, workspace.id)
    old_case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    (old_case_dir / "case-a.mgz").write_bytes(b"scan")
    db_session.add(
        Run(
            id="workspace-parent-run",
            workspace_id=workspace.id,
            case_id=None,
            created_by_user_id=context.user.id,
            scope_type=AssistantScope.workspace,
            status=RunStatus.completed,
            run_type="workspace_report",
            result_json={"case_ids": [case.id], "preview_path": "case-a.mgz"},
        )
    )
    db_session.commit()

    renamed = update_workspace(workspace.id, WorkspaceUpdateRequest(name="renamed-workspace"), db=db_session, context=context)

    new_case = db_session.get(Case, case.id)
    workspace_run = db_session.get(Run, "workspace-parent-run")
    assert renamed.id == workspace.id
    assert new_case is not None
    assert workspace_run is not None
    assert workspace_run.workspace_id == workspace.id
    assert workspace_run.result_json["case_ids"] == [case.id]
    assert workspace_run.result_json["preview_path"] == "case-a.mgz"
    assert not old_workspace_dir.exists()
    assert case_storage_dir(cases_module.settings, workspace.id, case.id).exists()


def test_rename_case_succeeds_with_foreign_keys_enabled(monkeypatch, tmp_path):
    db_session = fk_db_session()
    try:
        context, workspace, case = seed_fk_workspace_context(db_session)
        fs_root = tmp_path / "neurocade-data"
        outputs_dir = fs_root / "output"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
        ensure_workspace_storage_layout(cases_module.settings, workspace)
        case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
        (case_dir / "case-a.mgz").write_bytes(b"scan")
        artifact = Artifact(
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="case-a.mgz",
            relative_path="case-a.mgz",
            size_bytes=4,
        )
        db_session.add(artifact)
        db_session.commit()

        update_case(case.id, CaseUpdateRequest(title="case-b"), db=db_session, context=context)

        refreshed_artifact = db_session.get(Artifact, artifact.id)
        assert db_session.get(Case, case.id) is not None
        assert refreshed_artifact is not None
        assert refreshed_artifact.case_id == case.id
        assert refreshed_artifact.relative_path == "case-a.mgz"
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
        monkeypatch.setattr("api_service.routers.workspaces.settings.fs_data_root", fs_root)
        ensure_workspace_storage_layout(cases_module.settings, workspace)
        case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
        (case_dir / "case-a.mgz").write_bytes(b"scan")
        artifact = Artifact(
            case_id=case.id,
            workspace_id=workspace.id,
            kind=ArtifactKind.volume,
            name="case-a.mgz",
            relative_path="case-a.mgz",
            size_bytes=4,
        )
        db_session.add(artifact)
        db_session.commit()

        update_workspace(workspace.id, WorkspaceUpdateRequest(name="workspace-renamed"), db=db_session, context=context)

        refreshed_artifact = db_session.get(Artifact, artifact.id)
        refreshed_membership = db_session.query(WorkspaceMembership).filter_by(user_id=context.user.id).one()
        assert db_session.get(Workspace, workspace.id) is not None
        assert db_session.get(Case, case.id) is not None
        assert refreshed_membership.workspace_id == workspace.id
        assert refreshed_artifact is not None
        assert refreshed_artifact.workspace_id == workspace.id
        assert refreshed_artifact.case_id == case.id
        assert refreshed_artifact.relative_path == "case-a.mgz"
    finally:
        db_session.close()


def test_rename_workspace_rejects_active_case(seeded_context):
    db_session, context, workspace = seeded_context
    case = Case(
        id="active-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="active-case",
    )
    db_session.add(case)
    db_session.flush()
    ensure_case_storage_layout(cases_module.settings, case, workspace)
    db_session.add(
        Run(
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.running,
            run_type="fastsurfer_full",
            job_id=case.id,
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
            run_type="workspace_report",
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
    monkeypatch.setattr("api_service.routers.workspaces.log_event", lambda *args, **kwargs: None)

    extra_workspace = Workspace(
        id="workspace-extra",
        owner_user_id=context.user.id,
        name="shared-workspace",
        kind="shared",
        is_default=False,
    )
    db_session.add(extra_workspace)
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=extra_workspace.id, user_id=context.user.id, role=RoleEnum.owner, granted_by_user_id=context.user.id))
    db_session.add(
        Case(
            id="extra-case-id",
            workspace_id=extra_workspace.id,
            owner_user_id=context.user.id,
            title="case",
        )
    )
    workspace_dir = ensure_workspace_storage_layout(cases_module.settings, extra_workspace)
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
    assert created_case.id == response.case_id
    assert created_case.title == "chosen-name"
    assert response.title == "chosen-name"
    assert response.filenames == ["chosen-name.nii.gz"]
    assert upload_artifact.metadata_json["volume_role"] == "intensity"
    assert upload_event.artifact_id == upload_artifact.id
    assert upload_event.details_json["filenames"] == ["chosen-name.nii.gz"]
    assert db_session.query(Run).filter(Run.case_id == created_case.id).count() == 0
    stored_path = resolve_artifact_path(upload_artifact)
    assert gzip.decompress(stored_path.read_bytes()) == b"test-bytes"


def test_create_case_with_valid_gzipped_nifti_upload(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
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

    assert db_session.get(Case, response.case_id) is not None
    assert response.filenames == ["nifti-case.nii.gz"]
    assert artifact.name == "nifti-case.nii.gz"
    assert resolve_artifact_path(artifact).exists()


def test_save_generated_volume_registers_segmentation_artifact_with_collision_safe_name(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="drawing-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="drawing-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
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
    assert row.relative_path == "annotation-2.nii"
    assert row.size_bytes == len(b"nifti-bytes")


def test_failed_case_upload_does_not_reserve_case_title(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
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

    case = Case(
        id="stale-artifact-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="stale-artifact-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_dir.mkdir(parents=True, exist_ok=True)
    stale_rel = "scan.mgz"
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

    assert response.filenames == ["scan-2.mgz"]
    assert not (fs_root / stale_rel).exists()
    assert (case_dir / "scan-2.mgz").exists()


def test_create_case_with_dicom_upload_keeps_all_outputs_without_selecting_an_input(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
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
    assert response.filenames == ["localizer-1.nii.gz", "mprage-2.nii.gz"]
    assert len(artifacts) == 2
    assert [artifact.name for artifact in artifacts] == ["localizer-1.nii.gz", "mprage-2.nii.gz"]
    assert all(artifact.metadata_json["dicom_converted"] is True for artifact in artifacts)
    assert all("dicom_selected_input_candidate" not in artifact.metadata_json for artifact in artifacts)
    assert all("dicom_input_selection_reason" not in artifact.metadata_json for artifact in artifacts)
    assert all(resolve_artifact_path(artifact).exists() for artifact in artifacts)


def test_dicom_zip_upload_is_extracted_before_conversion(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
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

    assert response.filenames == ["t1-1.nii.gz"]


def test_deleted_case_title_can_be_reused(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="reusable-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="reusable-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
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
    assert response.case_id == matching_cases[0].id
    assert len(matching_cases) == 1


def test_add_case_upload_preserves_existing_case_outputs(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="existing-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()
    case_upload_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_upload_dir.mkdir(parents=True, exist_ok=True)
    original_upload = case_upload_dir / "existing-case.mgz"
    original_upload.write_bytes(b"old-bytes")
    original_artifact = Artifact(
        id="artifact-original-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="existing-case.mgz",
        relative_path=original_upload.name,
        mime_type="application/octet-stream",
        size_bytes=len(b"old-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(original_artifact)
    db_session.flush()
    old_volume_path = case_upload_dir / "mri" / "old-seg.mgz"
    old_volume_path.parent.mkdir(parents=True, exist_ok=True)
    old_volume_path.write_bytes(b"old-seg")
    old_log_path = case_upload_dir / "scripts" / "runs" / "old-run" / "stdout.log"
    old_log_path.parent.mkdir(parents=True, exist_ok=True)
    old_log_path.write_text("stale logs", encoding="utf-8")
    db_session.add_all(
        [
            Artifact(
                id="artifact-old-volume",
                case_id=case.id,
                workspace_id=workspace.id,
                kind=ArtifactKind.volume,
                name="old-seg.mgz",
                relative_path="mri/old-seg.mgz",
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
                relative_path="scripts/runs/old-run/stdout.log",
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
            run_type="fastsurfer_full",
            job_id=case.id,
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

    case_upload_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
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
    assert response.filenames == ["replacement.nii.gz"]
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


@pytest.mark.parametrize(
    ("tool_id", "expected_gpu"),
    [("fastsurfer_segmentation", True), ("mri_info", False)],
)
def test_start_run_persists_queued_run_before_runtime_handoff(
    seeded_context,
    monkeypatch,
    tmp_path,
    tool_id,
    expected_gpu,
):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_operations_module, "resolve_gpu_enabled", lambda preferred, **_kwargs: preferred)

    case = Case(
        id="run-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="run-case",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_dir.mkdir(parents=True, exist_ok=True)
    upload_path = case_dir / "run-case.mgz"
    upload_path.write_bytes(b"scan-bytes")
    artifact = Artifact(
        id="artifact-run-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="run-case.mgz",
        relative_path=upload_path.name,
        mime_type="application/octet-stream",
        size_bytes=len(b"scan-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.commit()

    def fake_submit_workflow(**kwargs):
        assert not db_session.in_transaction()
        queued_run = (
            db_session.query(Run)
            .filter(Run.case_id == case.id, Run.status == RunStatus.queued)
            .one_or_none()
        )
        assert queued_run is not None
        assert queued_run.job_id
        assert kwargs["workflow"].id == tool_id
        assert kwargs["job_id"] == queued_run.job_id
        assert kwargs["gpu_enabled"] is expected_gpu
        return kwargs["job_id"]

    monkeypatch.setattr(workflow_runs_module, "submit_neuroimaging_workflow", fake_submit_workflow)

    response = asyncio.run(
        start_run(
            StartRunRequest(
                case_id=case.id,
                tool_id=tool_id,
                input_artifact_ids=[artifact.id],
                output_name_overrides=(
                    {"whole_brain_segmentation": "Baseline segmentation"}
                    if tool_id == "fastsurfer_segmentation"
                    else {}
                ),
            ),
            db=db_session,
            context=context,
        )
    )

    run = db_session.get(Run, response.id)
    assert run is not None
    assert run.job_id
    assert run.result_json["status"] == "queued"
    assert run.input_json["execution"] == {"device": "cuda" if expected_gpu else "cpu"}
    assert run.input_json["output_name_overrides"] == (
        {"whole_brain_segmentation": "Baseline segmentation"}
        if tool_id == "fastsurfer_segmentation"
        else {}
    )
    assert (case_dir / "scripts" / "runs" / run.id / "stdout.log").is_file()
    assert (case_dir / "scripts" / "runs" / run.id / "stderr.log").is_file()


def test_manual_run_rejects_unknown_output_name_override(seeded_context):
    db_session, context, _workspace = seeded_context

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            start_run(
                StartRunRequest(
                    case_id="unused-case",
                    tool_id="fastsurfer_fast",
                    input_artifact_ids=["unused-artifact"],
                    output_name_overrides={"not_a_declared_output": "Custom name"},
                ),
                db=db_session,
                context=context,
            )
        )

    assert exc_info.value.status_code == 400
    assert "not_a_declared_output" in str(exc_info.value.detail)


def test_start_run_rejects_unavailable_required_cuda_before_creating_run(
    seeded_context,
    monkeypatch,
):
    db_session, context, _workspace = seeded_context

    def unavailable(_preferred, **_kwargs):
        raise run_operations_module.RuntimeGpuUnavailableError("CUDA unavailable for test")

    monkeypatch.setattr(run_operations_module, "resolve_gpu_enabled", unavailable)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            start_run(
                StartRunRequest(
                    case_id="missing-case",
                    tool_id="fastsurfer_full",
                    input_artifact_ids=["missing-artifact"],
                ),
                db=db_session,
                context=context,
            )
        )

    assert exc_info.value.status_code == 422
    assert "CUDA unavailable for test" in str(exc_info.value.detail)
    assert db_session.query(Run).count() == 0


def test_start_run_active_constraint_returns_conflict_when_precheck_races(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr(run_operations_module, "ensure_case_not_active", lambda *_args, **_kwargs: None)

    case = Case(
        id="active-conflict-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="active-conflict",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_dir.mkdir(parents=True, exist_ok=True)
    upload_path = case_dir / "active-conflict.mgz"
    upload_path.write_bytes(b"scan-bytes")
    artifact = Artifact(
        id="artifact-active-conflict-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="active-conflict.mgz",
        relative_path=upload_path.name,
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
                run_type="fastsurfer_segmentation",
            job_id=case.id,
            result_json={},
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            start_run(
                StartRunRequest(
                    case_id=case.id,
                    tool_id="fastsurfer_segmentation",
                    input_artifact_ids=[artifact.id],
                ),
                db=db_session,
                context=context,
            )
        )

    assert exc_info.value.status_code == 409


def test_run_analysis_rejects_segmentation_input(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    monkeypatch.setattr(uploads_module.settings, "fs_data_root", fs_root)
    case = Case(
        id="segmentation-input-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="segmentation-input",
    )
    db_session.add(case)
    db_session.flush()
    artifact_path = ensure_case_storage_layout(cases_module.settings, case, workspace) / "segmentation.mgz"
    artifact_path.write_bytes(b"segmentation")
    artifact = Artifact(
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name=artifact_path.name,
        relative_path="segmentation.mgz",
        size_bytes=artifact_path.stat().st_size,
        metadata_json={"volume_role": "segmentation"},
    )
    db_session.add(artifact)
    db_session.flush()

    with pytest.raises(HTTPException, match="intensity-volume") as exc_info:
        uploads_module._require_run_analysis_input_artifact(db_session, case, artifact.id)

    assert exc_info.value.status_code == 400


def test_add_case_upload_uses_unique_filename_for_duplicates(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="existing-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()
    case_upload_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_upload_dir.mkdir(parents=True, exist_ok=True)
    original_upload = case_upload_dir / "replacement.nii.gz"
    original_upload.write_bytes(b"old-bytes")
    original_artifact = Artifact(
        id="artifact-original-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="replacement.nii.gz",
        relative_path=original_upload.name,
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

    case_upload_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
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

    assert response.filenames == ["replacement-2.nii.gz"]
    assert upload_artifact is not None
    assert upload_artifact_names == {"replacement.nii.gz", "replacement-2.nii.gz"}
    assert gzip.decompress((case_upload_dir / "replacement-2.nii.gz").read_bytes()) == b"new-bytes"


def test_add_case_upload_rejects_existing_case_when_case_is_active(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)

    case = Case(
        id="running-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="running-case",
    )
    db_session.add(case)
    db_session.flush()
    ensure_case_storage_layout(cases_module.settings, case, workspace)
    db_session.add(
        Run(
            case_id=case.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.running,
            run_type="fastsurfer_full",
            job_id=case.id,
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
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="existing-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()

    old_case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    original_upload = old_case_dir / "existing-case.mgz"
    original_upload.write_bytes(b"scan-bytes")
    artifact = Artifact(
        id="artifact-input-upload",
        case_id=case.id,
        workspace_id=workspace.id,
        kind=ArtifactKind.volume,
        name="existing-case.mgz",
        relative_path="existing-case.mgz",
        mime_type="application/octet-stream",
        size_bytes=len(b"scan-bytes"),
        metadata_json={"volume_role": "intensity"},
    )
    db_session.add(artifact)
    db_session.flush()
    db_session.commit()

    response = update_case(
        case.id,
        CaseUpdateRequest(title="renamed-case"),
        db=db_session,
        context=context,
    )

    old_case_id = case.id
    new_case_id = case.id
    case = db_session.get(Case, case.id)
    assert case is not None
    db_session.refresh(artifact)
    renamed_dir = case_storage_dir(cases_module.settings, workspace.id, case.id)

    response_payload = response.model_dump()
    assert response_payload["id"] == new_case_id == old_case_id
    assert response_payload["title"] == "renamed-case"
    assert case.title == "renamed-case"
    assert not old_case_dir.exists()
    assert renamed_dir.exists()
    assert (renamed_dir / "existing-case.mgz").read_bytes() == b"scan-bytes"
    assert artifact.case_id == new_case_id
    assert artifact.name == "existing-case.mgz"
    assert artifact.relative_path == "existing-case.mgz"


def test_rename_case_keeps_workspace_scoped_references_stable(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="existing-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="existing-case",
    )
    db_session.add(case)
    db_session.flush()

    old_case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    (old_case_dir / "existing-case.mgz").write_bytes(b"scan-bytes")
    stored_reference = f"cases/{case.id}/existing-case.mgz"
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
            content_json={"value": f"Inspect {case.id} at {stored_reference}"},
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
            relative_path="workspace-analyses/ws-analysis/cases.json",
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
            run_type="workspace_report",
            input_json={"selected_case_id": case.id},
            result_json={"case_ids": [case.id], "preview_path": stored_reference},
        )
    )
    db_session.commit()

    update_case(case.id, CaseUpdateRequest(title="renamed-case"), db=db_session, context=context)

    new_case_id = case.id
    parent_run = db_session.get(Run, "workspace-parent-run")
    workspace_message = db_session.get(AssistantMessage, "workspace-message")
    workspace_artifact = db_session.get(Artifact, "workspace-artifact")
    assert parent_run is not None
    assert parent_run.input_json["selected_case_id"] == new_case_id
    assert parent_run.result_json["case_ids"] == [new_case_id]
    assert parent_run.result_json["preview_path"] == stored_reference
    assert workspace_message is not None
    assert workspace_message.content_json["value"] == f"Inspect {new_case_id} at {stored_reference}"
    assert workspace_message.metadata_json["selected_case_id"] == new_case_id
    assert workspace_artifact is not None
    assert workspace_artifact.metadata_json["selected_case_ids"] == [new_case_id]


def test_external_case_directory_rename_projects_name_without_database_write(seeded_context):
    db_session, context, workspace = seeded_context
    case = Case(
        id="stable-case-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="stable-case",
    )
    db_session.add(case)
    db_session.commit()
    old_case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    new_case_dir = old_case_dir.with_name("external-name")
    old_case_dir.replace(new_case_dir)

    get_case_for_user(db_session, case.id, context.user.id)

    assert case.title == "external-name"
    assert not db_session.is_modified(case)
    assert db_session.execute(select(Case.title).where(Case.id == case.id)).scalar_one() == "stable-case"
    assert not old_case_dir.exists()
    assert case_storage_dir(cases_module.settings, workspace.id, case.id) == new_case_dir


def test_delete_case_removes_db_row_and_storage(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="delete-me-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="delete-me",
    )
    db_session.add(case)
    db_session.flush()

    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_dir.mkdir(parents=True, exist_ok=True)
    upload_path = case_dir / "delete-me.mgz"
    upload_path.write_bytes(b"scan-bytes")
    db_session.commit()

    response = asyncio.run(delete_case(case.id, db=db_session, context=context))

    assert response == {"deleted": case.id}
    assert db_session.get(Case, case.id) is None
    assert not case_dir.exists()


def test_delete_case_restores_storage_when_database_commit_fails(seeded_context, monkeypatch, tmp_path):
    db_session, context, workspace = seeded_context
    fs_root = tmp_path / "neurocade-data"
    outputs_dir = fs_root / "output"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cases_module.settings, "fs_data_root", fs_root)
    monkeypatch.setattr("api_service.cases.operations.log_event", lambda *args, **kwargs: None)

    case = Case(
        id="restore-me-id",
        workspace_id=workspace.id,
        owner_user_id=context.user.id,
        title="restore-me",
    )
    db_session.add(case)
    db_session.flush()
    case_dir = ensure_case_storage_layout(cases_module.settings, case, workspace)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "restore-me.mgz").write_bytes(b"scan-bytes")
    db_session.commit()

    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(delete_case(case.id, db=db_session, context=context))

    assert db_session.get(Case, case.id) is not None
    assert (case_dir / "restore-me.mgz").read_bytes() == b"scan-bytes"


def test_queue_status_requires_workspace_manager_role(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context
    membership = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == workspace.id, WorkspaceMembership.user_id == context.user.id)
        .one()
    )
    membership.role = RoleEnum.user
    db_session.commit()

    def fake_queue_status():
        return {"active": 1, "queued": 2, "total": 3}

    monkeypatch.setattr("api_service.routers.cases.job_manager.queue_status", fake_queue_status)

    with pytest.raises(HTTPException) as exc_info:
        queue_status(workspace_id=workspace.id, db=db_session, context=context)

    assert exc_info.value.status_code == 403


def test_queue_status_returns_counts_for_workspace_owner(seeded_context, monkeypatch):
    db_session, context, workspace = seeded_context

    def fake_queue_status():
        return {"active": 1, "queued": 2, "total": 3}

    monkeypatch.setattr("api_service.routers.cases.job_manager.queue_status", fake_queue_status)

    result = queue_status(workspace_id=workspace.id, db=db_session, context=context)

    assert result == {"active": 1, "queued": 2, "total": 3}
