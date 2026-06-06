"""Test demo case script behavior for NeuroCade."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "process_demo_case.sh"


def test_process_demo_case_help_exposes_quick_call() -> None:
    """Verify process demo case help exposes quick call.

    Returns
    -------
    None
        This function does not return a value.
    """
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Quick call:" in result.stdout
    assert "./scripts/process_demo_case.sh" in result.stdout
    assert "--build-only" in result.stdout
    assert "sample_case/create_fastsurfer_sample_case.sh" in result.stdout
