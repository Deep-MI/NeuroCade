"""Test case resolver behavior for NeuroCade."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime_tools.case_resolver import resolve_case_mount_from_gui_state  # noqa: E402


def test_resolve_case_mount_from_gui_state_uses_storage_manifests(tmp_path):
    data_root = tmp_path / "neurocade-data"
    output_root = data_root / "output"
    case_dir = output_root / "workspaces" / "ws-1" / "cases" / "case-a"
    (case_dir / "mri").mkdir(parents=True)
    (case_dir.parent.parent / ".neurocade-workspace.json").write_text(json.dumps({"id": "workspace-id"}))
    (case_dir / ".neurocade-case.json").write_text(json.dumps({"id": "case-id"}))
    (case_dir / "mri" / "orig.mgz").write_bytes(b"volume")

    resolved = resolve_case_mount_from_gui_state(
        {"workspace_id": "workspace-id", "case_id": "case-id"},
        data_root=data_root,
        output_root=output_root,
    )

    assert resolved == case_dir.resolve()


def test_resolve_case_mount_from_gui_state_rejects_paths_outside_data_root(tmp_path):
    data_root = tmp_path / "neurocade-data"
    output_root = data_root / "output"
    outside = tmp_path / "outside"
    outside.mkdir()

    assert (
        resolve_case_mount_from_gui_state(
            {"workspace_id": "../../outside", "case_id": "case-a"},
            data_root=data_root,
            output_root=output_root,
        )
        is None
    )
