"""Test bootstrap seed behavior for NeuroCade."""

from pathlib import Path
import sys
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "api-service"))

from api_service import bootstrap as bootstrap_module  # noqa: E402
from backend_common.db import Base, Case, User, Workspace, WorkspaceMembership  # noqa: E402
from backend_common.workspace_bootstrap import ensure_personal_workspace  # noqa: E402


def test_seed_demo_state_creates_dev_user_and_default_workspace(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fs_data_root = tmp_path / "neurocade-data"
    outputs_dir = fs_data_root / "output"
    outputs_dir.mkdir(parents=True)

    settings = SimpleNamespace(
        clerk_jwks_url="",
        local_auth_user_id="demo-user",
        local_auth_email="demo@example.com",
        local_auth_name="Demo User",
        fs_data_root=fs_data_root,
        outputs_dir=outputs_dir,
    )
    monkeypatch.setattr(bootstrap_module, "settings", settings)

    with Session() as db:
        bootstrap_module.seed_demo_state(db)

        user = db.get(User, "demo-user")
        workspace = db.get(Workspace, "personal-workspace")
        assert user is not None
        assert user.email == "demo@example.com"
        assert workspace is not None
        assert workspace.owner_user_id == "demo-user"
        assert workspace.name == "personal-workspace"
        assert db.query(WorkspaceMembership).filter_by(workspace_id="personal-workspace", user_id="demo-user").count() == 1


def test_personal_workspace_bootstrap_can_use_clerk_readable_user_slug(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with Session() as db:
        first = User(id="clerk-user-one", external_auth_id="clerk-user-one", email="alice@example.com", full_name="Alice Example")
        second = User(id="clerk-user-two", external_auth_id="clerk-user-two", email="alice@other.example", full_name="Alice Other")
        db.add_all([first, second])
        db.flush()

        first_workspace = ensure_personal_workspace(db, first, readable_user_slug=True)
        second_workspace = ensure_personal_workspace(db, second, readable_user_slug=True)
        db.commit()

        assert first_workspace.id.startswith("alice-workspace-")
        assert second_workspace.id.startswith("alice-workspace-")
        assert first_workspace.id != second_workspace.id
        assert first_workspace.owner_user_id == first.id
        assert second_workspace.owner_user_id == second.id
        assert db.query(WorkspaceMembership).filter_by(workspace_id=second_workspace.id, user_id=second.id).count() == 1


def test_seed_demo_state_does_not_index_canonical_output_cases(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    fs_data_root = tmp_path / "neurocade-data"
    outputs_dir = fs_data_root / "output"
    outputs_dir.mkdir(parents=True)

    canonical_case_dir = outputs_dir / "workspaces" / "ws-demo" / "cases" / "canonical-job-123"
    canonical_case_dir.mkdir(parents=True)
    (canonical_case_dir / "subject.txt").write_text("Canonical Case", encoding="utf-8")
    (canonical_case_dir / "status.json").write_text('{"status": "finished"}', encoding="utf-8")
    (canonical_case_dir / "mri").mkdir()
    (canonical_case_dir / "mri" / "orig.mgz").write_bytes(b"mgz")

    settings = SimpleNamespace(
        clerk_jwks_url="",
        local_auth_user_id="demo-user",
        local_auth_email="demo@example.com",
        local_auth_name="Demo User",
        fs_data_root=fs_data_root,
        outputs_dir=outputs_dir,
    )
    monkeypatch.setattr(bootstrap_module, "settings", settings)

    with Session() as db:
        demo_user = User(
            id="demo-user",
            external_auth_id="demo-user",
            email="demo@example.com",
            full_name="Demo User",
        )
        demo_workspace = Workspace(
            id="ws-demo",
            owner_user_id=demo_user.id,
            name="ws-demo",
            kind="personal",
            is_default=True,
            status="active",
        )
        db.add_all([demo_user, demo_workspace])
        db.commit()

        bootstrap_module.seed_demo_state(db)

        assert db.query(Case).count() == 0
