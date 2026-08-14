"""Live Chromium coverage for MRI snapshots sent to the configured vision model."""

from __future__ import annotations

from gui_helpers import send_chat_message, take_screenshot

pytest_plugins = ["conftest_gui"]


def test_agent_interprets_real_mri_snapshots(page, screenshot_dir):
    """The browser sends all three real viewer planes and the model identifies them."""
    processed_case = page.evaluate(
        """async () => {
          const response = await fetch('/api/app/cases');
          const cases = await response.json();
          return cases.find(candidate => candidate.artifact_count >= 10) ?? null;
        }"""
    )
    assert processed_case is not None, "A processed case is required for the live MRI vision test"
    origin = page.url.split("/workspaces/", 1)[0]
    page.goto(
        f"{origin}/workspaces/{processed_case['workspace_id']}/cases/{processed_case['id']}",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    page.wait_for_selector("canvas", state="visible", timeout=20_000)
    page.wait_for_selector("[aria-label='Loading imaging data']", state="hidden", timeout=60_000)
    page.wait_for_timeout(3_000)
    take_screenshot(page, "agent_mri_vision_source", screenshot_dir)

    response = send_chat_message(
        page,
        "@mri Inspect the three viewer snapshots. What body organ and imaging modality are shown? Answer in one sentence.",
        timeout=120_000,
    )

    normalized = response.lower()
    assert "brain" in normalized
    assert "mri" in normalized or "magnetic resonance" in normalized or "t1" in normalized
    take_screenshot(page, "agent_mri_vision", screenshot_dir)
