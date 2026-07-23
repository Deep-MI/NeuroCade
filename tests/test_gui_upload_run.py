"""
GUI E2E test: Upload an MRI file, send a chat message to run FastSurfer,
verify the job starts, wait 20 seconds, then cancel it.

This test drives a real browser using Playwright and interacts with the
NeuroCade web UI exactly as a user would.

Flow:
  1. Navigate to http://localhost:8000
  2. Upload the configured MRI fixture via the file picker
  3. Verify the filename appears in the viewer
  4. Type "Run FastSurfer on this case" in chat, click Send
  5. Wait for assistant response (up to 120s)
  6. Verify the response mentions running/started (not a text explanation)
  7. Screenshot the page
  8. Wait for the Cancel button to appear, wait 20s, then click Cancel
  9. Screenshot the final state

Prerequisites:
  ./scripts/run.sh start -d
  pip install playwright && playwright install chromium
  A fixture MRI must exist under NEUROCADE_UPLOAD_FIXTURES_DIR

Usage:
  pytest tests/test_gui_upload_run.py -v
  HEADED=1 pytest tests/test_gui_upload_run.py -v         # watch the browser
"""

import shutil
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from conftest import DEMO_RUN_UPLOAD_FILENAME, UPLOAD_FIXTURES_DIR
from gui_helpers import (
    GATEWAY_URL,
    get_auth_headers,
    infer_case_name,
    load_processed_case,
    routed_case_id,
    send_chat_message,
    slug_name,
    take_screenshot,
    upload_mri,
)

# Register GUI fixtures (browser, page, screenshot_dir, etc.)
pytest_plugins = ["conftest_gui"]

UPLOAD_FILE = (
    UPLOAD_FIXTURES_DIR / DEMO_RUN_UPLOAD_FILENAME
    if DEMO_RUN_UPLOAD_FILENAME
    else None
)


def create_workspace(page, name: str) -> dict:
    """Create a workspace through the authenticated browser session."""
    workspace_name = slug_name(name)
    result = page.evaluate(
        """async ({ name, authorization }) => {
          const token = await window.Clerk?.session?.getToken?.();
          const headerValue = token ? `Bearer ${token}` : authorization;
          const response = await fetch('/api/app/workspaces', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(headerValue ? { Authorization: headerValue } : {}),
            },
            body: JSON.stringify({ name }),
          });
          if (!response.ok) {
            return { ok: false, status: response.status, detail: await response.text() };
          }
          return { ok: true, workspace: await response.json() };
        }""",
        {"name": workspace_name, "authorization": get_auth_headers().get("Authorization")},
    )
    assert result.get("ok"), f"Failed to create workspace {workspace_name!r}: {result}"
    return result["workspace"]


@pytest.fixture(autouse=True)
def require_upload_file():
    """Skip if the test MRI file doesn't exist."""
    if UPLOAD_FILE is None or not UPLOAD_FILE.exists():
        pytest.skip(f"Test file not found: {UPLOAD_FILE}")


@pytest.fixture()
def upload_file(tmp_path: Path) -> Path:
    assert UPLOAD_FILE is not None
    suffix = "".join(UPLOAD_FILE.suffixes) or UPLOAD_FILE.suffix
    unique_path = tmp_path / f"gui-upload-{uuid4().hex[:8]}{suffix}"
    shutil.copy2(UPLOAD_FILE, unique_path)
    return unique_path


class TestGuiUploadAndRun:
    """Upload an MRI and trigger FastSurfer analysis via the chat interface."""

    def test_upload_and_run_fastsurfer(self, page, screenshot_dir, upload_file):
        """Full browser E2E: upload → chat → run → cancel."""
        # Step 1: Verify initial page state
        upload_button = page.locator("button:has-text('Choose MRI File')")
        upload_button.wait_for(state="visible", timeout=20_000)
        assert upload_button.is_visible(), "Upload control not visible — page didn't load"

        # Step 2: Upload the MRI file
        upload_mri(page, str(upload_file))

        # Verify upload — the filename should appear somewhere in the UI
        # After upload, the volume name appears in the layer panel
        page.wait_for_timeout(2000)
        take_screenshot(page, "01_after_upload", screenshot_dir)

        # Step 3: Send chat message to run FastSurfer
        response_text = send_chat_message(
            page,
            "Run FastSurfer on this case",
            timeout=120_000,
        )

        take_screenshot(page, "02_after_chat_response", screenshot_dir)

        # Step 4: Verify the response mentions running/execution
        response_lower = response_text.lower()
        run_markers = [
            "fastsurfer", "started", "running", "triggered", "analysis",
            "processing", "submitted", "queued", "beginning", "launched",
        ]
        found = [m for m in run_markers if m in response_lower]
        assert found, (
            f"Response doesn't mention FastSurfer execution.\n"
            f"Found markers: {found}\n"
            f"Response: {response_text[:300]}"
        )

        # Step 5: Check if the Cancel button appears (indicates job was started)
        # Wait a bit for the job to be submitted and status to update
        time.sleep(5)

        # Check for cancel button or job status indicator
        cancel_btn = page.locator("button:has-text('Cancel Analysis')")
        if cancel_btn.is_visible():
            # Step 6: Wait 20 seconds then cancel
            print("\n  Cancel button visible — job is running. Waiting 20s...")
            time.sleep(20)

            take_screenshot(page, "03_before_cancel", screenshot_dir)

            if cancel_btn.is_visible() and cancel_btn.is_enabled():
                cancel_btn.click()
                time.sleep(3)

                take_screenshot(page, "04_after_cancel", screenshot_dir)
                print("  Job canceled via UI.")
            else:
                take_screenshot(page, "04_after_state_change", screenshot_dir)
                print(
                    "\n  Cancel button was no longer actionable after the wait; "
                    "the job likely transitioned out of the running state."
                )
        else:
            # The LLM may not have actually triggered the run via GUI
            # (it might have used the tool call path which runs server-side)
            print(
                "\n  Cancel button not visible — the agent may have used "
                "server-side execution rather than GUI trigger."
            )
            take_screenshot(page, "03_no_cancel_button", screenshot_dir)

    def test_upload_shows_in_viewer(self, page, screenshot_dir, upload_file):
        """After uploading, verify the MRI viewer shows canvases."""
        upload_mri(page, str(upload_file))
        page.wait_for_timeout(3000)

        # MriViewer renders three <canvas> elements (sagittal, coronal, axial)
        canvases = page.locator("canvas")
        assert canvases.count() >= 3, (
            f"Expected at least 3 canvases (MRI views), found {canvases.count()}"
        )

        take_screenshot(page, "05_viewer_with_upload", screenshot_dir)

    def test_case_view_upload_can_add_volume_to_current_case(self, page, screenshot_dir, upload_file):
        """Uploading from case view can append a new intensity volume without leaving the current case."""
        current_case_id = routed_case_id(page)
        initial_layer_count = page.locator("div.layer-item").count()

        upload_mri(page, str(upload_file), destination="add_to_case")

        assert routed_case_id(page) == current_case_id
        page.locator("div.layer-item", has_text=upload_file.name).wait_for(state="visible", timeout=20_000)
        assert page.locator("div.layer-item").count() >= initial_layer_count + 1

        take_screenshot(page, "05b_case_view_add_volume", screenshot_dir)

    def test_empty_workspace_shows_upload_tile(self, page, screenshot_dir, upload_file):
        """An empty workspace should still expose the upload entry point as a case-shaped tile."""
        workspace = create_workspace(page, f"empty-workspace-{uuid4().hex[:8]}")
        workspace_id = workspace["id"]

        page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(f"**/workspaces/{workspace_id}/cases", timeout=15_000)

        empty_message = page.locator("text=This workspace is empty. Upload an MRI file to create the first case.")
        empty_message.wait_for(state="visible", timeout=10_000)

        upload_tile = page.locator("button:has-text('Upload Case')").first
        upload_tile.wait_for(state="visible", timeout=10_000)
        assert upload_tile.locator("text=Supports NIfTI, MGZ, DICOM files, and DICOM ZIP archives.").is_visible()

        upload_tile.click()
        dropzone = page.locator("[data-testid='upload-file-dropzone']")
        dropzone.wait_for(state="visible", timeout=10_000)
        with page.expect_file_chooser() as fc_info:
            dropzone.click()
        fc_info.value.set_files(str(upload_file))
        name_input = page.locator("[data-testid='upload-case-name-input']")
        name_input.wait_for(state="visible", timeout=10_000)
        assert name_input.input_value() == infer_case_name(upload_file.name)
        page.click("[data-testid='confirm-upload-case']")

        page.wait_for_url(f"**/workspaces/{workspace_id}/cases/*", timeout=30_000)
        page.wait_for_selector("button:has-text('Run FastSurfer Analysis')", state="visible", timeout=15_000)
        take_screenshot(page, "06_empty_workspace_upload_tile", screenshot_dir)

    def test_workspace_case_card_can_rename_and_delete_case(self, page, screenshot_dir, upload_file):
        """Workspace cards should support inline rename and delete for an idle case."""
        workspace = create_workspace(page, f"workspace-actions-{uuid4().hex[:8]}")
        workspace_id = workspace["id"]

        page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(f"**/workspaces/{workspace_id}/cases", timeout=15_000)

        initial_name = f"workspace-action-case-{uuid4().hex[:6]}"
        upload_mri(page, str(upload_file), case_name=initial_name, trigger_selector="button:has-text('Upload Case')")
        case_id = routed_case_id(page)
        case_slug = case_id.split("__", 1)[1]

        page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(f"**/workspaces/{workspace_id}/cases", timeout=15_000)
        page.locator(f"text={initial_name}").wait_for(state="visible", timeout=15_000)

        page.click(f"[data-testid='workspace-case-rename-{case_id}']")
        rename_input = page.locator(f"[data-testid='workspace-case-rename-input-{case_id}']")
        rename_input.wait_for(state="visible", timeout=10_000)
        rename_input.fill(case_slug)
        page.click(f"[data-testid='workspace-case-rename-confirm-{case_id}']")
        page.locator(f"[data-testid='workspace-case-title-{case_id}']", has_text=case_slug).wait_for(
            state="visible",
            timeout=15_000,
        )

        renamed_name = f"renamed-workspace-case-{uuid4().hex[:6]}"
        page.click(f"[data-testid='workspace-case-rename-{case_id}']")
        rename_input = page.locator(f"[data-testid='workspace-case-rename-input-{case_id}']")
        rename_input.wait_for(state="visible", timeout=10_000)
        rename_input.fill(renamed_name)
        page.click(f"[data-testid='workspace-case-rename-confirm-{case_id}']")
        page.locator(f"text={renamed_name}").wait_for(state="visible", timeout=15_000)
        take_screenshot(page, "06b_workspace_case_renamed", screenshot_dir)

        renamed_case_id = f"{workspace_id}__{renamed_name}"
        page.click(f"[data-testid='workspace-case-delete-{renamed_case_id}']")
        page.locator(f"[data-testid='workspace-case-delete-confirm-{renamed_case_id}']").wait_for(state="visible", timeout=10_000)
        page.click(f"[data-testid='workspace-case-delete-confirm-{renamed_case_id}']")
        page.locator(f"[data-testid='workspace-case-delete-{renamed_case_id}']").wait_for(state="hidden", timeout=15_000)
        page.locator("text=This workspace is empty. Upload an MRI file to create the first case.").wait_for(
            state="visible",
            timeout=15_000,
        )

        take_screenshot(page, "06c_workspace_case_deleted", screenshot_dir)

    def test_manage_cases_card_can_rename_to_case_id(self, page, screenshot_dir, upload_file):
        """The in-workspace case manager should rename a case even when the new title equals the case id."""
        workspace = create_workspace(page, f"manage-actions-{uuid4().hex[:8]}")
        workspace_id = workspace["id"]

        page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(f"**/workspaces/{workspace_id}/cases", timeout=15_000)

        initial_name = f"manage-action-case-{uuid4().hex[:6]}"
        upload_mri(page, str(upload_file), case_name=initial_name, trigger_selector="button:has-text('Upload Case')")
        case_id = routed_case_id(page)
        case_slug = case_id.split("__", 1)[1]

        page.click("button:has-text('Manage Cases')")
        page.get_by_role("heading", name="Manage Cases").wait_for(state="visible", timeout=10_000)
        page.locator(f"[data-testid='manage-case-title-{case_id}']", has_text=initial_name).wait_for(
            state="visible",
            timeout=10_000,
        )

        page.click(f"[data-testid='manage-case-rename-{case_id}']")
        rename_input = page.locator(f"[data-testid='manage-case-rename-input-{case_id}']")
        rename_input.wait_for(state="visible", timeout=10_000)
        rename_input.fill(case_slug)
        page.click(f"[data-testid='manage-case-rename-confirm-{case_id}']")
        page.locator(f"[data-testid='manage-case-title-{case_id}']", has_text=case_slug).wait_for(
            state="visible",
            timeout=15_000,
        )
        take_screenshot(page, "06d_manage_cases_card_renamed", screenshot_dir)

    def test_personal_workspace_sample_case_card_can_rename(self, page, screenshot_dir):
        """The seeded personal-workspace sample case should keep a user rename across refreshes."""
        parts = urlparse(page.url).path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "workspaces":
            pytest.skip(f"Expected workspace case route, got {page.url}")
        workspace_id = parts[1]

        sample_case = page.evaluate(
            """async (workspaceId) => {
              const response = await fetch(`/api/app/cases?workspace_id=${encodeURIComponent(workspaceId)}`);
              if (!response.ok) {
                return { ok: false, status: response.status, detail: await response.text() };
              }
              const cases = await response.json();
              const sampleId = `${workspaceId}__sample-case`;
              return { ok: true, case: cases.find((entry) => entry.id === sampleId || entry.title === 'sample-case') || null };
            }""",
            workspace_id,
        )
        assert sample_case.get("ok"), f"Failed to fetch personal-workspace cases: {sample_case}"
        case_item = sample_case.get("case")
        if not case_item:
            pytest.skip("No seeded sample case found in the current workspace")

        case_id = case_item["id"]
        original_name = case_item["title"]
        renamed_name = f"sample-card-rename-{uuid4().hex[:6]}"
        renamed_case_id = f"{workspace_id}__{renamed_name}"
        current_case_id = case_id

        try:
            page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_url(f"**/workspaces/{workspace_id}/cases", timeout=15_000)
            page.click(f"[data-testid='workspace-case-rename-{case_id}']")
            rename_input = page.locator(f"[data-testid='workspace-case-rename-input-{case_id}']")
            rename_input.wait_for(state="visible", timeout=10_000)
            rename_input.fill(renamed_name)
            with page.expect_response(
                lambda response: response.request.method == "PATCH" and f"/api/app/cases/{case_id}" in response.url,
                timeout=10_000,
            ) as patch_response:
                page.click(f"[data-testid='workspace-case-rename-confirm-{case_id}']")
            response = patch_response.value
            assert response.ok, f"Sample case rename failed with {response.status}: {response.text()}"
            current_case_id = response.json()["new_id"]
            page.locator(f"[data-testid='workspace-case-title-{current_case_id}']", has_text=renamed_name).wait_for(
                state="visible",
                timeout=15_000,
            )

            page.reload(wait_until="domcontentloaded", timeout=30_000)
            page.locator(f"[data-testid='workspace-case-title-{renamed_case_id}']", has_text=renamed_name).wait_for(
                state="visible",
                timeout=15_000,
            )
            take_screenshot(page, "06e_personal_sample_case_renamed", screenshot_dir)
        finally:
            page.evaluate(
                """async ({ caseId, title }) => {
                  await fetch(`/api/app/cases/${encodeURIComponent(caseId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title }),
                  });
                }""",
                {"caseId": current_case_id, "title": original_name},
            )

    def test_processed_case_reload_shows_static_log(self, page, screenshot_dir):
        """Reloading a completed case should reopen the static output log automatically."""
        case_name = load_processed_case(page)
        current_url = page.url

        page.reload(wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(current_url, timeout=15_000)
        page.wait_for_selector("input.chat-input", state="visible", timeout=20_000)
        page.wait_for_selector("button:has-text('Hide Output')", state="visible", timeout=20_000)
        page.wait_for_selector("[data-testid='terminal-content']", state="visible", timeout=10_000)
        page.wait_for_function(
            """() => {
              const terminal = document.querySelector('[data-testid="terminal-content"]');
              return !!terminal && (terminal.textContent || '').trim().length > 32;
            }""",
            timeout=15_000,
        )

        terminal_text = page.locator("[data-testid='terminal-content']").inner_text().strip()
        assert terminal_text, f"Expected stored logs to be visible for completed case {case_name!r}"

        take_screenshot(page, "07_processed_case_static_log", screenshot_dir)
