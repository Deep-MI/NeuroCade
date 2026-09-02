"""Browser E2E coverage for typed assistant surface commands."""

from __future__ import annotations

import os

import pytest
from gui_helpers import send_chat_message, take_screenshot

pytest_plugins = ["conftest_gui"]
pytestmark = [
    pytest.mark.gui,
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("RUN_LLM_E2E", "").strip().lower() not in {"1", "true", "yes", "on"},
        reason="Live assistant evaluations require RUN_LLM_E2E=1",
    ),
]


def _surface_row(page, filename: str):
    return page.locator(
        f'[data-testid="viewer-layer-item"][data-layer-type="surface"]:has-text("{filename}")'
    ).first


def _set_surface_hidden(page, filename: str) -> None:
    row = _surface_row(page, filename)
    row.wait_for(state="visible", timeout=20_000)
    visibility = row.get_by_test_id("viewer-layer-visibility")
    if (visibility.get_attribute("aria-label") or "").startswith("Hide "):
        visibility.click()


def _wait_for_surface_visible(page, filename: str) -> None:
    row = _surface_row(page, filename)
    row.wait_for(state="visible", timeout=20_000)
    page.wait_for_function(
        """({ filename }) => {
          const rows = Array.from(document.querySelectorAll(
            '[data-testid="viewer-layer-item"][data-layer-type="surface"]'
          ));
          const row = rows.find(candidate => candidate.textContent?.includes(filename));
          return row?.querySelector('[data-testid="viewer-layer-visibility"]')
            ?.getAttribute('aria-label')?.startsWith('Hide ') ?? false;
        }""",
        arg={"filename": filename},
        timeout=15_000,
    )


def test_agent_applies_pial_surface_preset(page, screenshot_dir):
    """The LLM queues a typed preset and the browser applies and acknowledges it."""
    processed_case = page.evaluate(
        """async () => {
          const response = await fetch('/api/app/cases');
          const cases = await response.json();
          return cases.find(candidate => candidate.artifact_count >= 10) ?? null;
        }"""
    )
    if processed_case:
        case_id = processed_case["id"]
        workspace_id = processed_case["workspace_id"]
        origin = page.url.split("/workspaces/", 1)[0]
        page.goto(
            f"{origin}/workspaces/{workspace_id}/cases/{case_id}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
    page.wait_for_selector("canvas", state="visible", timeout=15_000)
    _set_surface_hidden(page, "lh.pial")
    _set_surface_hidden(page, "rh.pial")
    page.wait_for_timeout(3_000)

    response = send_chat_message(
        page,
        "Use gui_apply_view_preset with preset pial_surfaces now.",
        timeout=120_000,
    )

    _wait_for_surface_visible(page, "lh.pial")
    _wait_for_surface_visible(page, "rh.pial")
    assert "pial" in response.lower() or "surface" in response.lower()
    take_screenshot(page, "agent_pial_surface_preset", screenshot_dir)
