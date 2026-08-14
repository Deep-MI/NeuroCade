"""Test bootstrap seed behavior for NeuroCade."""

import sys
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "api-service"))

from api_service import bootstrap as bootstrap_module  # noqa: E402

from backend_common.db import Base, User, Workspace, WorkspaceMembership  # noqa: E402
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
        workspace = db.query(Workspace).filter_by(owner_user_id="demo-user", is_default=True).one()
        assert user is not None
        assert user.email == "demo@example.com"
        assert workspace is not None
        assert workspace.owner_user_id == "demo-user"
        assert workspace.name == "personal-workspace"
        assert db.query(WorkspaceMembership).filter_by(workspace_id=workspace.id, user_id="demo-user").count() == 1

        user.email = "stale@example.com"
        user.full_name = '"Demo User"'
        db.commit()
        bootstrap_module.seed_demo_state(db)
        db.refresh(user)

        assert user.email == "demo@example.com"
        assert user.full_name == "Demo User"


def test_personal_workspace_bootstrap_uses_opaque_ids(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    with Session() as db:
        first = User(id="clerk-user-one", external_auth_id="clerk-user-one", email="alice@example.com", full_name="Alice Example")
        second = User(id="clerk-user-two", external_auth_id="clerk-user-two", email="alice@other.example", full_name="Alice Other")
        db.add_all([first, second])
        db.flush()

        settings = SimpleNamespace(outputs_dir=tmp_path / "output")
        first_workspace = ensure_personal_workspace(db, settings, first)
        second_workspace = ensure_personal_workspace(db, settings, second)
        db.commit()

        assert first_workspace.id != second_workspace.id
        assert first_workspace.name == "personal-workspace"
        assert second_workspace.name == "personal-workspace-2"
        assert first_workspace.owner_user_id == first.id
        assert second_workspace.owner_user_id == second.id
        assert db.query(WorkspaceMembership).filter_by(workspace_id=second_workspace.id, user_id=second.id).count() == 1
