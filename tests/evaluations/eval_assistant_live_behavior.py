"""Deterministic behavioral evaluations against the configured live LLM."""

from __future__ import annotations

import os
from uuid import uuid4

import numpy as np
import pytest
from conftest import chat_send, upload_path_as_case_via_api
from nibabel.loadsave import save
from nibabel.nifti1 import Nifti1Image

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("RUN_LLM_E2E", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="Live assistant evaluations require RUN_LLM_E2E=1",
    ),
]


@pytest.fixture(scope="module")
def live_cases(disposable_workspace, tmp_path_factory):
    """Create one isolated assistant thread per evaluation and remove it afterward."""
    source = tmp_path_factory.mktemp("assistant-live-eval") / "probe.nii.gz"
    save(Nifti1Image(np.zeros((2, 2, 2), dtype=np.uint8), np.eye(4)), source)
    cases = []
    for index in range(4):
        case = upload_path_as_case_via_api(
            disposable_workspace["id"],
            source,
            title=f"assistant-eval-{index}",
            content_type="application/gzip",
        )
        filename = case["filenames"][0]
        cases.append(
            {"id": case["case_id"], "workspace_id": disposable_workspace["id"], "filename": filename}
        )
    return cases


def evaluate(live_case: dict, prompt: str) -> tuple[str, list[dict]]:
    result = chat_send(
        [{"role": "user", "content": prompt}],
        workspace_id=live_case["workspace_id"],
        case_id=live_case["id"],
        gui_session_id=f"live-eval-{uuid4()}",
        timeout=180,
    )
    return result["message"]["content"], result.get("tool_calls_log", [])


def test_file_listing_uses_one_authoritative_tool_call(live_cases):
    live_case = live_cases[0]
    content, calls = evaluate(
        live_case,
        "Call case_file_tree exactly once for path ., then name the NIfTI file you found. Do not reuse earlier evidence.",
    )
    matching = [call for call in calls if call["name"] == "case_file_tree"]
    assert len(matching) == 1
    assert matching[0]["arguments"].get("path") == "."
    assert live_case["filename"] in matching[0]["result"]
    assert live_case["filename"] in content


def test_lut_lookup_returns_exact_numeric_label(live_cases):
    live_case = live_cases[1]
    content, calls = evaluate(
        live_case,
        "Call freesurfer_lut exactly once with query 17 and report the exact label name. Do not call any other tool.",
    )
    assert [call["name"] for call in calls] == ["freesurfer_lut"]
    assert "17" in calls[0]["result"]
    assert "left-hippocampus" in calls[0]["result"].lower()
    assert "left-hippocampus" in content.lower()


def test_missing_file_is_not_retried(live_cases):
    live_case = live_cases[2]
    path = f"/case/qa-probe-{uuid4().hex}.txt"
    content, calls = evaluate(
        live_case,
        f"Read {path} exactly once, then report the result without retrying or calling another tool.",
    )
    assert [call["name"] for call in calls] == ["read"], content
    assert "error" in calls[0]["result"].lower()
    assert any(marker in content.lower() for marker in ("not exist", "no such file", "failed", "error"))


def test_tool_discovery_does_not_execute_workflow(live_cases):
    live_case = live_cases[3]
    content, calls = evaluate(
        live_case,
        "Use tool_search to find the configured fast segmentation workflow. Do not execute it.",
    )
    assert calls
    assert calls[0]["name"] == "tool_search"
    assert all(call["name"] != "tool_call" for call in calls)
    assert "fastsurfer_segmentation" in "\n".join(call["result"] for call in calls)
    assert "fastsurfer" in content.lower() or "segmentation" in content.lower()
