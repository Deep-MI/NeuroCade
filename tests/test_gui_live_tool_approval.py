"""Live browser coverage for an approved configured neuroimaging tool."""

from __future__ import annotations

import os

import pytest
from conftest import DATA_ROOT, upload_path_as_case_via_api

pytest_plugins = ["conftest_gui"]


@pytest.fixture()
def mri_info_case(disposable_workspace):
    """Create an isolated case/thread around the public sample MRI."""
    source = DATA_ROOT / "output" / "workspaces" / "personal-workspace" / "cases" / "sample-case" / "mri" / "orig.mgz"
    if not source.exists():
        pytest.skip("The public sample orig.mgz is required for live mri_info QA")
    case = upload_path_as_case_via_api(
        disposable_workspace["id"],
        source,
        title="mri-info-live",
        upload_filename="orig.mgz",
    )
    return {"id": case["case_id"], "workspace_id": disposable_workspace["id"], "filename": case["filename"]}


@pytest.mark.skipif(
    os.environ.get("RUN_LLM_E2E", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="Live assistant tests require RUN_LLM_E2E=1",
)
def test_agent_runs_mri_info_after_browser_approval(page, mri_info_case):
    """A real approval resumes the private turn and executes mri_info once."""
    origin = page.url.split("/workspaces/", 1)[0]
    page.goto(
        f"{origin}/workspaces/{mri_info_case['workspace_id']}/cases/{mri_info_case['id']}",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    page.locator("input.chat-input").fill(
        f"Run the configured mri_info workflow on /case/{mri_info_case['filename']} and report its dimensions and voxel size."
    )
    page.get_by_role("button", name="Send").click()

    confirmation = page.get_by_role("group", name="Confirm assistant action")
    confirmation.wait_for(state="visible", timeout=120_000)
    assert "Mri Info" in confirmation.inner_text()
    assert "orig.mgz" in confirmation.inner_text()
    confirmation.get_by_role("button", name="Start workflow").click()
    confirmation.wait_for(state="detached", timeout=10_000)

    page.wait_for_selector(
        "div.chat-message.info:has-text('Assistant is')",
        state="hidden",
        timeout=180_000,
    )
    tool_summary = page.locator("div.chat-message.tool-calls").last
    tool_summary.wait_for(state="visible", timeout=10_000)
    assert "tool_call" in tool_summary.inner_text()
    response = page.locator("div.chat-message.assistant").last.inner_text().lower()
    assert any(marker in response for marker in ("dimension", "voxel", "resolution", "320", "0.8"))
