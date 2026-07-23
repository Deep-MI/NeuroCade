"""Test scan indexing behavior for NeuroCade."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend_common.case_storage import build_case_id, case_slug_from_id, case_storage_dir, ensure_case_storage_layout
from backend_common.db import Artifact, ArtifactKind, Base, Case, RoleEnum, User, Workspace, WorkspaceMembership
from backend_common.scan import index_case_files_from_storage


def test_case_slug_from_id_rejects_unprefixed_case_id():
    with pytest.raises(ValueError, match="canonical"):
        case_slug_from_id("workspace-1", "case-1")


def test_index_reuses_existing_case_for_matching_case_id(tmp_path):
    """Index updates an existing case instead of creating a duplicate."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fs_data_root = tmp_path / "neurocade-data"
    settings = SimpleNamespace(
        fs_data_root=fs_data_root,
        outputs_dir=fs_data_root / "output",
    )
    settings.outputs_dir.mkdir(parents=True)
    (settings.outputs_dir / "existing-case").mkdir()

    with Session() as db:
        owner = User(
            id="owner-user",
            external_auth_id="owner-user",
            email="owner@example.com",
            full_name="Owner User",
        )
        other_user = User(
            id="other-user",
            external_auth_id="other-user",
            email="other@example.com",
            full_name="Other User",
        )
        owner_workspace = Workspace(
            id="ws-owner",
            owner_user_id=owner.id,
            name="ws-owner",
            kind="personal",
            is_default=True,
            status="active",
        )
        other_workspace = Workspace(
            id="ws-other",
            owner_user_id=other_user.id,
            name="ws-other",
            kind="personal",
            is_default=False,
            status="active",
        )
        existing_case = Case(
            id=build_case_id("ws-owner", "existing-case"),
            workspace_id=owner_workspace.id,
            owner_user_id=owner.id,
            title="existing-case",
        )
        db.add_all([owner, other_user, owner_workspace, other_workspace, existing_case])
        db.commit()

        index_case_files_from_storage(
            db,
            settings,
            user_id=other_user.id,
            case_id=build_case_id("ws-owner", "existing-case"),
            workspace_id=other_workspace.id,
            case_title="new-case-title",
        )
        db.commit()

        cases = db.query(Case).all()
        assert len(cases) == 1
        assert cases[0].id == build_case_id("ws-owner", "existing-case")
        assert cases[0].workspace_id == owner_workspace.id
        assert cases[0].title == "new-case-title"

        memberships = (
            db.query(WorkspaceMembership)
            .filter(WorkspaceMembership.workspace_id == owner_workspace.id)
            .all()
        )
        assert len(memberships) == 1
        assert memberships[0].user_id == owner.id
        assert memberships[0].role == RoleEnum.owner


def test_ensure_case_storage_layout_uses_local_case_slug_path(tmp_path):
    """Case storage uses readable workspace and local case slugs."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fs_data_root = tmp_path / "neurocade-data"
    settings = SimpleNamespace(
        fs_data_root=fs_data_root,
        outputs_dir=fs_data_root / "output",
    )
    settings.outputs_dir.mkdir(parents=True)

    with Session() as db:
        user = User(
            id="user-1",
            external_auth_id="user-1",
            email="user@example.com",
            full_name="User",
        )
        workspace = Workspace(
            id="ws-1",
            owner_user_id=user.id,
            name="ws-1",
            kind="personal",
            is_default=True,
            status="active",
        )
        case = Case(
            id=build_case_id("ws-1", "case-1"),
            workspace_id=workspace.id,
            owner_user_id=user.id,
            title="case-1",
        )
        db.add_all([user, workspace, case])
        db.commit()

        case_dir = ensure_case_storage_layout(db, settings, case, workspace)

        assert case_dir == case_storage_dir(settings, workspace.id, case.id)
        assert case_dir == settings.outputs_dir / "workspaces" / workspace.id / "cases" / "case-1"
        assert case_dir.exists()


def test_index_case_files_from_storage_is_idempotent_for_artifacts(tmp_path):
    """Repeated filesystem indexing does not create duplicate artifacts."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fs_data_root = tmp_path / "neurocade-data"
    settings = SimpleNamespace(
        fs_data_root=fs_data_root,
        outputs_dir=fs_data_root / "output",
    )
    settings.outputs_dir.mkdir(parents=True)

    with Session() as db:
        user = User(
            id="user-1",
            external_auth_id="user-1",
            email="user@example.com",
            full_name="User",
        )
        workspace = Workspace(
            id="ws-1",
            owner_user_id=user.id,
            name="ws-1",
            kind="personal",
            is_default=True,
            status="active",
        )
        case = Case(
            id=build_case_id("ws-1", "case-1"),
            workspace_id=workspace.id,
            owner_user_id=user.id,
            title="case-1",
        )
        db.add_all([user, workspace, case])
        db.flush()
        case_dir = ensure_case_storage_layout(db, settings, case, workspace)
        upload_path = case_dir / "case-1.mgz"
        upload_path.write_bytes(b"upload")
        output_path = case_dir / "mri" / "orig.mgz"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"orig")
        surface_path = case_dir / "surf" / "lh.pial"
        surface_path.parent.mkdir(parents=True, exist_ok=True)
        surface_path.write_bytes(b"surface")
        annotation_path = case_dir / "label" / "lh.aparc.DKTatlas.mapped.annot"
        annotation_path.parent.mkdir(parents=True, exist_ok=True)
        annotation_path.write_bytes(b"annotation")
        db.commit()

        for _index in range(2):
            index_case_files_from_storage(
                db,
                settings,
                user_id=user.id,
                case_id=case.id,
                workspace_id=workspace.id,
                case_title=case.title,
            )
            db.commit()

        artifacts = db.query(Artifact).filter(Artifact.case_id == case.id).all()
        relative_paths = [artifact.relative_path for artifact in artifacts]

        assert len(relative_paths) == len(set(relative_paths))
        assert db.query(Artifact).filter(Artifact.case_id == case.id, Artifact.kind == ArtifactKind.volume).count() == 2
        surface_artifact = db.query(Artifact).filter(Artifact.case_id == case.id, Artifact.name == "lh.pial").one()
        assert surface_artifact.kind == ArtifactKind.derived
        assert surface_artifact.metadata_json["layer_role"] == "surface"
        annotation_artifact = db.query(Artifact).filter(Artifact.case_id == case.id, Artifact.name == "lh.aparc.DKTatlas.mapped.annot").one()
        assert annotation_artifact.kind == ArtifactKind.derived
        assert annotation_artifact.metadata_json["layer_role"] == "surface-annotation"


def test_index_case_files_skips_symlinked_outputs(tmp_path):
    """A symlink in one case must not backfill an artifact pointing at another case."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fs_data_root = tmp_path / "neurocade-data"
    settings = SimpleNamespace(
        fs_data_root=fs_data_root,
        outputs_dir=fs_data_root / "output",
    )
    settings.outputs_dir.mkdir(parents=True)

    with Session() as db:
        user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(
            id="ws-1",
            owner_user_id=user.id,
            name="ws-1",
            kind="personal",
            is_default=True,
            status="active",
        )
        case = Case(id=build_case_id(workspace.id, "case-1"), workspace_id=workspace.id, owner_user_id=user.id, title="case-1")
        other_case = Case(id=build_case_id(workspace.id, "case-2"), workspace_id=workspace.id, owner_user_id=user.id, title="case-2")
        db.add_all([user, workspace, case, other_case])
        db.flush()
        case_dir = ensure_case_storage_layout(db, settings, case, workspace)
        other_case_dir = ensure_case_storage_layout(db, settings, other_case, workspace)
        target = other_case_dir / "mri" / "secret.mgz"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"secret")
        symlink = case_dir / "mri" / "linked-secret.mgz"
        symlink.parent.mkdir(parents=True)
        try:
            symlink.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")
        db.commit()

        index_case_files_from_storage(
            db,
            settings,
            user_id=user.id,
            case_id=case.id,
            workspace_id=workspace.id,
            case_title=case.title,
        )
        db.commit()

        artifacts = db.query(Artifact).filter(Artifact.case_id == case.id).all()
        assert all("secret.mgz" not in artifact.relative_path for artifact in artifacts)
