"""Offline integrity and approval checks for the isolated agent runner."""
import hashlib
import json

import pytest

from benchmarks.workflow_setup.run_agent import summarize, verify_fixtures


def fixtures(tmp_path):
    hashes = {}
    for task in ("blur", "thalamus"):
        directory = tmp_path / task
        directory.mkdir()
        (directory / "input").write_bytes(b"fixture")
        hashes[f"{task}/input"] = hashlib.sha256(b"fixture").hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({"files_sha256": hashes}))
    return tmp_path


@pytest.mark.parametrize("mutation", ["modified", "extra", "missing", "symlink"])
def test_fixture_verification_rejects_changed_inputs(tmp_path, mutation):
    root = fixtures(tmp_path)
    assert verify_fixtures(root)
    path = root / "blur/input"
    if mutation == "modified":
        path.write_bytes(b"changed")
    elif mutation == "extra":
        (root / "blur/extra").write_bytes(b"extra")
    elif mutation == "missing":
        path.unlink()
    else:
        path.unlink()
        path.symlink_to(root / "thalamus/input")
    with pytest.raises(ValueError):
        verify_fixtures(root)


def test_only_actionable_save_approval_counts():
    assert not summarize({"message": {"content": "May I save?"}}, [])['setup_approval_reached']
    assert not summarize({"approval_request": {"name": "tool_call"}}, [])['setup_approval_reached']
    assert summarize({"approval_request": {"name": "tool_config_upsert"}}, [])['setup_approval_reached']
