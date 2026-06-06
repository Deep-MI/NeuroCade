"""
GUI regression test for MRI overlays when the segmentation header differs from
the base intensity volume.

The local debug route synthesizes two NIfTI volumes:
  - an intensity image with a bright cube at the center
  - a binary segmentation representing the same cube in world space, but with
    a translated header

The viewer should still report label 1 at the target voxel once affine-aware
sampling is working.
"""

from __future__ import annotations

import pytest

from gui_helpers import GATEWAY_URL, take_screenshot

pytest_plugins = ["conftest_gui"]


def get_panel_boxes(page, axis: str):
    """Return the view frame and canvas boxes for a viewer axis."""
    panel = page.locator(f'.view-panel[data-view-axis="{axis}"]')
    frame = panel.locator('.view-content').bounding_box()
    canvas = panel.locator('canvas').bounding_box()
    assert frame is not None
    assert canvas is not None
    return frame, canvas


def get_center(box: dict[str, float]) -> tuple[float, float]:
    return box["x"] + (box["width"] / 2), box["y"] + (box["height"] / 2)


def test_dev_header_mismatch_overlay_aligns(services_up, browser, screenshot_dir):
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
    )
    page = context.new_page()

    try:
        page.goto(f"{GATEWAY_URL}/_dev/mri-viewer/header-mismatch", wait_until="domcontentloaded", timeout=30_000)
        heading = page.locator("h1:has-text('Segmentation Header Mismatch Repro')")
        try:
            heading.wait_for(state="visible", timeout=10_000)
        except Exception as exc:  # pragma: no cover - skip path depends on runtime stack mode
            pytest.skip(f"Dev MRI header-mismatch route unavailable: {exc}")

        page.wait_for_function(
            """() => document.querySelector('[data-testid="debug-current-label"]')?.textContent?.trim() !== 'loading'""",
            timeout=30_000,
        )

        reset_button = page.locator("[data-testid='mri-reset-view']")
        status = page.locator("[data-testid='debug-alignment-status']").inner_text().strip()
        label = page.locator("[data-testid='debug-current-label']").inner_text().strip()
        coord = page.locator("[data-testid='debug-current-coordinate']").inner_text().strip()
        sagittal_frame, sagittal_canvas = get_panel_boxes(page, "x")
        coronal_frame, coronal_canvas = get_panel_boxes(page, "y")
        axial_frame, axial_canvas = get_panel_boxes(page, "z")

        take_screenshot(page, "08_mri_header_mismatch_alignment", screenshot_dir)

        assert status == "Aligned at target"
        assert label == "1"
        assert coord == "[20, 20, 12]"
        assert reset_button.is_disabled()
        assert abs(sagittal_frame["width"] - sagittal_frame["height"]) <= 2
        assert abs(axial_frame["width"] - axial_frame["height"]) <= 2
        assert abs(sagittal_canvas["width"] - sagittal_canvas["height"]) >= 8
        assert abs(axial_canvas["width"] - axial_frame["width"]) <= 2
        assert abs(axial_canvas["height"] - axial_frame["height"]) <= 2

        sagittal_center = get_center(sagittal_canvas)
        page.mouse.move(*sagittal_center)
        page.mouse.down(button="right")
        page.mouse.move(sagittal_center[0], sagittal_center[1] - 90, steps=10)
        page.mouse.up(button="right")

        sagittal_canvas_zoomed = get_panel_boxes(page, "x")[1]
        coronal_canvas_zoomed = get_panel_boxes(page, "y")[1]
        axial_canvas_zoomed = get_panel_boxes(page, "z")[1]

        assert not reset_button.is_disabled()
        assert sagittal_canvas_zoomed["width"] > sagittal_canvas["width"] + 20
        assert coronal_canvas_zoomed["width"] > coronal_canvas["width"] + 20
        assert axial_canvas_zoomed["width"] > axial_canvas["width"] + 20

        sagittal_zoomed_center = get_center(sagittal_canvas_zoomed)
        coronal_zoomed_center = get_center(coronal_canvas_zoomed)
        axial_zoomed_center = get_center(axial_canvas_zoomed)
        page.mouse.move(*sagittal_zoomed_center)
        page.mouse.down(button="middle")
        page.mouse.move(sagittal_zoomed_center[0] + 45, sagittal_zoomed_center[1] + 25, steps=8)
        page.mouse.up(button="middle")

        sagittal_canvas_panned = get_panel_boxes(page, "x")[1]
        coronal_canvas_panned = get_panel_boxes(page, "y")[1]
        axial_canvas_panned = get_panel_boxes(page, "z")[1]
        sagittal_panned_center = get_center(sagittal_canvas_panned)
        coronal_panned_center = get_center(coronal_canvas_panned)
        axial_panned_center = get_center(axial_canvas_panned)

        assert abs(sagittal_panned_center[0] - sagittal_zoomed_center[0]) >= 20
        assert abs(sagittal_panned_center[1] - sagittal_zoomed_center[1]) >= 10
        assert abs(coronal_panned_center[0] - coronal_zoomed_center[0]) <= 3
        assert abs(coronal_panned_center[1] - coronal_zoomed_center[1]) <= 3
        assert abs(axial_panned_center[0] - axial_zoomed_center[0]) <= 3
        assert abs(axial_panned_center[1] - axial_zoomed_center[1]) <= 3

        take_screenshot(page, "09_mri_header_mismatch_zoomed_panned", screenshot_dir)

        reset_button.click()

        sagittal_canvas_reset = get_panel_boxes(page, "x")[1]
        coronal_canvas_reset = get_panel_boxes(page, "y")[1]
        axial_canvas_reset = get_panel_boxes(page, "z")[1]

        assert reset_button.is_disabled()
        assert abs(sagittal_canvas_reset["width"] - sagittal_canvas["width"]) <= 2
        assert abs(sagittal_canvas_reset["height"] - sagittal_canvas["height"]) <= 2
        assert abs(coronal_canvas_reset["width"] - coronal_canvas["width"]) <= 2
        assert abs(coronal_canvas_reset["height"] - coronal_canvas["height"]) <= 2
        assert abs(axial_canvas_reset["width"] - axial_canvas["width"]) <= 2
        assert abs(axial_canvas_reset["height"] - axial_canvas["height"]) <= 2
        assert abs(sagittal_canvas_reset["x"] - sagittal_canvas["x"]) <= 2
        assert abs(sagittal_canvas_reset["y"] - sagittal_canvas["y"]) <= 2

        take_screenshot(page, "10_mri_header_mismatch_reset_view", screenshot_dir)
    finally:
        context.close()
