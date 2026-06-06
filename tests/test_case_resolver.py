"""Test case resolver behavior for NeuroCade."""

from pathlib import Path
from types import SimpleNamespace
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime_tools.case_resolver import (  # noqa: E402
    case_container_path_from_local_path,
    resolve_case_mount_from_db,
    resolve_case_mount_from_gui_state,
)
from backend_common.case_storage import build_case_id  # noqa: E402
from backend_common.db import Base, Case, User, Workspace  # noqa: E402


def test_resolve_case_mount_from_gui_state_maps_id_first_path(tmp_path):
    data_root = tmp_path / "neurocade-data"
    output_root = data_root / "output"
    case_dir = output_root / "workspaces" / "ws-1" / "cases" / "case-a"
    (case_dir / "mri").mkdir(parents=True)
    volume = case_dir / "mri" / "orig.mgz"
    volume.write_bytes(b"volume")

    resolved = resolve_case_mount_from_gui_state(
        {"current_workspace_id": "ws-1", "current_case_id": "ws-1__case-a"},
        data_root=data_root,
        output_root=output_root,
    )

    assert resolved == case_dir.resolve()
    assert case_container_path_from_local_path(volume, resolved) == "/case/mri/orig.mgz"


def test_resolve_case_mount_from_gui_state_rejects_paths_outside_data_root(tmp_path):
    data_root = tmp_path / "neurocade-data"
    output_root = data_root / "output"
    outside = tmp_path / "outside"
    outside.mkdir()

    assert (
        resolve_case_mount_from_gui_state(
            {"current_workspace_id": "../../outside", "current_case_id": "case-a"},
            data_root=data_root,
            output_root=output_root,
        )
        is None
    )


def test_resolve_case_mount_from_db_uses_canonical_case_storage(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    settings = SimpleNamespace(fs_data_root=tmp_path / "neurocade-data", outputs_dir=tmp_path / "neurocade-data" / "output")
    try:
        user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(
            id="ws-1",
            owner_user_id=user.id,
            name="personal-workspace",
            kind="personal",
            is_default=True,
            status="active",
        )
        case = Case(id=build_case_id(workspace.id, "case-1"), workspace_id=workspace.id, owner_user_id=user.id, title="case-1")
        db.add_all([user, workspace, case])
        db.commit()

        resolved = resolve_case_mount_from_db(db, settings, case, workspace)

        assert resolved is not None
        assert resolved == (settings.outputs_dir / "workspaces" / "ws-1" / "cases" / "case-1")
        assert resolved.exists()
    finally:
        db.close()
