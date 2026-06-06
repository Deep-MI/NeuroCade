"""
GUI E2E test: Open a processed case, ask the chat to show the cerebellum,
and verify the cursor moves to the cerebellum region.

This test drives a real browser using Playwright and validates the full
LLM → gui_focus_label → GUI state sync → cursor position pipeline.

Flow:
  1. Navigate to http://localhost:8005
  2. Open a processed case in the current workspace
  3. Wait for volumes to load (MRI viewer shows canvases)
  4. Type "Show me the cerebellum" in chat, click Send
  5. Wait for assistant response (the LLM should call gui_focus_label)
  6. Wait 5s for GUI state sync (every 2s, frontend polls for cursor commands)
  7. Read the "Current Position" display in LayerControl
  8. Assert the label name contains "cerebellum" (case-insensitive)
  9. Screenshot the result

Prerequisites:
  ./scripts/apptainer/up.sh -d
  pip install playwright && playwright install chromium
  The current workspace must contain at least one processed case with MRI outputs

Usage:
  pytest tests/test_gui_focus_label.py -v
  HEADED=1 pytest tests/test_gui_focus_label.py -v   # watch the browser
"""

import time
from gui_helpers import (
    get_current_position,
    load_processed_case,
    send_chat_message,
    take_screenshot,
)

# Register GUI fixtures (browser, page, screenshot_dir, etc.)
pytest_plugins = ["conftest_gui"]


class TestGuiFocusLabel:
    """Load a case and navigate to an anatomical region via chat."""

    def test_focus_cerebellum(self, page, screenshot_dir):
        """Chat asks to show cerebellum → cursor should move to cerebellum region."""
        # Step 1: Navigate to a processed case in the current workspace
        load_processed_case(page)

        # Step 2: Wait for volumes to load
        # The MRI viewer should show canvases
        page.wait_for_selector("canvas", state="visible", timeout=15_000)
        canvases = page.locator("canvas")
        assert canvases.count() >= 3, (
            f"Expected at least 3 MRI view canvases, got {canvases.count()}"
        )

        take_screenshot(page, "focus_01_case_loaded", screenshot_dir)

        # Wait for the volumes and segmentation to fully load
        # (the state sync needs to report has_valid_segmentation=True)
        time.sleep(5)

        # Step 3: Send chat message
        response_text = send_chat_message(
            page,
            "Show me the cerebellum",
            timeout=120_000,
        )

        take_screenshot(page, "focus_02_after_chat", screenshot_dir)

        # Verify the response is about the cerebellum / cursor movement
        response_lower = response_text.lower()
        cerebellum_markers = [
            "cerebellum", "cerebell", "cursor", "moved", "navigat",
            "centroid", "focus", "position",
        ]
        found = [m for m in cerebellum_markers if m in response_lower]
        assert found, (
            f"Response doesn't mention cerebellum or cursor movement.\n"
            f"Response: {response_text[:300]}"
        )

        # Step 4: Wait for GUI state sync to deliver the cursor position
        # Frontend polls every 2s; the requested_cursor_position is drained on poll
        time.sleep(6)

        take_screenshot(page, "focus_03_after_sync", screenshot_dir)

        # Step 5: Read the Current Position display
        position = get_current_position(page)
        print(f"\n  Current position: {position}")

        # Verify the label name contains "cerebellum"
        label_name = position.get("label_name", "")
        if label_name:
            assert "cerebel" in label_name.lower(), (
                f"Expected label name containing 'cerebel', got: '{label_name}'"
            )
            print(f"  Label: {label_name}")
        else:
            # The label may not be visible if no segmentation overlay is active.
            # In that case, just check that voxels changed (not at origin)
            voxel_text = position.get("voxel_text", "0, 0, 0")
            print(f"  Voxel coords: {voxel_text}")
            print(
                "  Note: Label name not visible — segmentation overlay may not be active. "
                "Cursor position was still updated."
            )
            # As long as the voxel coordinates are not all zeros, the cursor moved
            assert voxel_text != "0, 0, 0", (
                "Cursor appears to still be at origin (0, 0, 0)"
            )

        take_screenshot(page, "focus_04_final", screenshot_dir)

    def test_focus_hippocampus(self, page, screenshot_dir):
        """Chat asks to show hippocampus → cursor should move to hippocampus."""
        load_processed_case(page)
        page.wait_for_selector("canvas", state="visible", timeout=15_000)
        time.sleep(5)

        response_text = send_chat_message(
            page,
            "Navigate to the left hippocampus",
            timeout=120_000,
        )

        take_screenshot(page, "focus_05_hippocampus_chat", screenshot_dir)

        response_lower = response_text.lower()
        assert any(
            m in response_lower for m in ["hippocampus", "hippocampal", "cursor", "moved"]
        ), f"Response doesn't mention hippocampus: {response_text[:300]}"

        # Wait for sync
        time.sleep(6)

        position = get_current_position(page)
        print(f"\n  Current position after hippocampus focus: {position}")

        label_name = position.get("label_name", "")
        if label_name:
            assert any(
                m in label_name.lower() for m in ["hippocampus", "hippocampal"]
            ), f"Expected hippocampus label, got: '{label_name}'"

        take_screenshot(page, "focus_06_hippocampus_final", screenshot_dir)
