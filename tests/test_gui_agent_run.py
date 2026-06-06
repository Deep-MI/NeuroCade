"""
GUI E2E test: Upload an MRI file via the UI, ask the agent to run FastSurfer,
and verify the GUI reacts properly (terminal opens, status transitions, case
appears in the run list).

This test validates the full round-trip:
  1. User uploads MRI file → eager upload to server
  2. runtime state sync picks up the uploaded file + case ID
  3. User asks agent "Run FastSurfer on this case"
  4. Agent calls gui_run_fastsurfer tool
  5. runtime handler sets requested_run_fastsurfer in gui_state
  6. Frontend picks up the command and submits /run to the backend
  7. GUI updates: terminal panel opens, status transitions, case list refreshes

Prerequisites:
  ./scripts/apptainer/up.sh -d
  pip install playwright && playwright install chromium
  A fixture MRI must exist under NEUROCADE_UPLOAD_FIXTURES_DIR

Usage:
  pytest tests/test_gui_agent_run.py -v
  HEADED=1 pytest tests/test_gui_agent_run.py -v   # watch the browser
"""

import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

import pytest
from conftest import DEMO_RUN_UPLOAD_FILENAME, UPLOAD_FIXTURES_DIR

from gui_helpers import (
    infer_case_name,
    routed_case_id,
    send_chat_message,
    take_screenshot,
    upload_mri,
)

# Register GUI fixtures (browser, page, screenshot_dir, etc.)
pytest_plugins = ["conftest_gui"]

UPLOAD_FILE = UPLOAD_FIXTURES_DIR / DEMO_RUN_UPLOAD_FILENAME if DEMO_RUN_UPLOAD_FILENAME else None


@pytest.fixture(autouse=True)
def require_upload_file():
    """Skip if the test MRI file doesn't exist."""
    if UPLOAD_FILE is None or not UPLOAD_FILE.exists():
        pytest.skip(f"Test file not found: {UPLOAD_FILE}")


@pytest.fixture()
def upload_file(tmp_path: Path) -> Path:
    assert UPLOAD_FILE is not None
    suffix = "".join(UPLOAD_FILE.suffixes) or UPLOAD_FILE.suffix
    unique_path = tmp_path / f"gui-agent-upload-{uuid4().hex[:8]}{suffix}"
    shutil.copy2(UPLOAD_FILE, unique_path)
    return unique_path


def poll_case_status(page, case_id: str, timeout: int = 30) -> str:
    """Poll job status via the authenticated browser context until it resolves."""
    deadline = time.time() + timeout
    last_status = "unknown"
    while time.time() < deadline:
        try:
            result = page.evaluate(
                """async (caseId) => {
                  const token = await window.Clerk?.session?.getToken?.();
                  const response = await fetch(`/api/app/cases/${caseId}/runs`, {
                    headers: token ? { Authorization: `Bearer ${token}` } : {},
                  });
                  if (!response.ok) {
                    return { status: `http_${response.status}` };
                  }
                  const runs = await response.json();
                  return { status: runs[0]?.status ?? 'unknown' };
                }""",
                case_id,
            )
            if isinstance(result, dict):
                last_status = result.get("status", "unknown")
                if last_status in ("queued", "running", "starting", "finished",
                                   "completed", "error", "canceled"):
                    return last_status
        except Exception:
            pass
        time.sleep(1)
    return last_status


class TestGuiAgentTriggeredRun:
    """Upload an MRI and trigger FastSurfer run through the chat agent."""

    def test_eager_upload_sets_case_id(self, page, screenshot_dir, upload_file):
        """After uploading, the workspace stays interactive and ready for chat."""
        upload_mri(page, str(upload_file))
        page.wait_for_timeout(3000)

        take_screenshot(page, "agent_run_01_after_upload", screenshot_dir)
        assert page.locator("input.chat-input").is_visible()

    def test_agent_triggers_fastsurfer_run(self, page, screenshot_dir, upload_file):
        """Full browser E2E: upload → chat agent → FastSurfer run starts."""
        # Step 1: Upload the MRI file
        upload_mri(page, str(upload_file))
        page.wait_for_timeout(3000)
        take_screenshot(page, "agent_run_01_uploaded", screenshot_dir)

        upload_case_name = infer_case_name(upload_file.name)
        current_case_id = routed_case_id(page)
        print(f"\n  Case name after upload: {upload_case_name}")
        print(f"  Routed case id after upload: {current_case_id}")

        # Step 2: Ask the agent to run FastSurfer
        response_text = send_chat_message(
            page,
            "Run FastSurfer on this case",
            timeout=120_000,
        )
        take_screenshot(page, "agent_run_02_chat_response", screenshot_dir)

        # Step 3: Verify the agent response mentions running/execution
        response_lower = response_text.lower()
        run_markers = [
            "fastsurfer", "started", "running", "triggered", "analysis",
            "processing", "submitted", "queued", "pipeline", "launched",
        ]
        found = [m for m in run_markers if m in response_lower]
        assert found, (
            f"Response doesn't mention FastSurfer execution.\n"
            f"Response: {response_text[:500]}"
        )
        print(f"  Agent response markers: {found}")

        # Step 4: Verify GUI updates — terminal/output panel should be visible
        # Wait for the frontend to pick up requested_run_fastsurfer and submit.
        deadline = time.time() + 15
        terminal_visible = False
        cancel_visible = False
        rerun_visible = False
        error_status = False
        while time.time() < deadline:
            terminal_visible = page.locator("button:has-text('Hide Output')").is_visible()
            cancel_visible = page.locator("button:has-text('Cancel Analysis')").is_visible()
            rerun_visible = page.locator("button:has-text('Rerun')").is_visible()
            error_status = page.locator("button:has-text('Analysis Finished')").is_visible()
            if terminal_visible or cancel_visible or rerun_visible or error_status:
                break
            page.wait_for_timeout(500)
        take_screenshot(page, "agent_run_03_after_submission", screenshot_dir)

        # The "View Output" button should now indicate the terminal is open,
        # or the terminal panel itself should be visible
        print(f"  Terminal visible: {terminal_visible}")
        print(f"  Cancel visible: {cancel_visible}")
        print(f"  Rerun visible: {rerun_visible}")

        # At least one of these should be true — the run was submitted
        assert terminal_visible or cancel_visible or rerun_visible or error_status, (
            "Expected the GUI to show the output terminal or status change "
            "after agent triggered FastSurfer, but none of the expected "
            "UI elements are visible."
        )

        # Step 5: Check job status via REST API
        status = poll_case_status(page, current_case_id, timeout=30)
        print(f"  Job status: {status}")
        assert status != "unknown", (
            f"Job status never transitioned from 'unknown' for routed case {current_case_id}"
        )

        take_screenshot(page, "agent_run_04_final", screenshot_dir)

    def test_workspace_chat_is_available(self, page, screenshot_dir):
        """The routed workspace should expose workspace chat without a manual mode toggle."""
        workspace_url = page.url.rsplit("/", 1)[0]
        page.goto(workspace_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url("**/workspaces/*/cases", timeout=30_000)
        page.wait_for_selector("input.chat-input", state="visible", timeout=20_000)
        page.wait_for_function(
            """() => document.querySelector('input.chat-input')?.getAttribute('placeholder') === 'Ask about the workspace...'""",
            timeout=20_000,
        )

        chat_input = page.locator("input.chat-input")
        assert chat_input.is_visible(), "Chat input not visible in routed workspace"
        assert chat_input.get_attribute("placeholder") == "Ask about the workspace..."
        assert page.locator("button:has-text('Case Mode')").count() == 0
        assert page.locator("button:has-text('Workspace Mode')").count() == 0
        assert page.locator("button:has-text('Clear')").count() == 1
        take_screenshot(page, "agent_run_workspace_chat_ready", screenshot_dir)

    def test_case_chat_is_case_mode_without_toggle(self, page, screenshot_dir):
        """Case routes should keep the case-scoped placeholder and remove mode buttons."""
        page.wait_for_selector("input.chat-input", state="visible", timeout=20_000)
        page.wait_for_function(
            """() => document.querySelector('input.chat-input')?.getAttribute('placeholder') === 'Ask about the scan...'""",
            timeout=20_000,
        )
        chat_input = page.locator("input.chat-input")
        assert chat_input.is_visible(), "Chat input not visible in routed case view"
        assert chat_input.get_attribute("placeholder") == "Ask about the scan..."
        assert page.locator("button:has-text('Case Mode')").count() == 0
        assert page.locator("button:has-text('Workspace Mode')").count() == 0
        assert page.locator("button:has-text('Clear')").count() == 1
        take_screenshot(page, "agent_run_case_chat_ready", screenshot_dir)

    def test_clear_chat_ignores_stale_history_response(self, page, screenshot_dir):
        """A delayed initial history load must not repopulate messages after Clear succeeds."""
        stale_text = "Old assistant history should not reappear after clearing."
        stale_history = {
            "thread_id": "thread-stale-history",
            "messages": [{"role": "assistant", "content": stale_text}],
        }
        page.add_init_script(
            f"""
            (() => {{
              const staleHistory = {json.dumps(stale_history)};
              let releaseHistory = () => {{}};
              const waitForHistory = new Promise((resolve) => {{
                releaseHistory = resolve;
              }});
              window.__assistantHistoryRequested = false;
              window.__assistantHistoryDeleteRequested = false;
              window.__releaseAssistantHistory = () => releaseHistory();
              const originalFetch = window.fetch.bind(window);
              window.fetch = async (input, init) => {{
                const request = input instanceof Request ? input : null;
                const url = typeof input === 'string' ? input : request?.url ?? '';
                const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
                if (url.includes('/api/app/assistant/history?')) {{
                  if (method === 'GET') {{
                    window.__assistantHistoryRequested = true;
                    await waitForHistory;
                    return new Response(JSON.stringify(staleHistory), {{
                      status: 200,
                      headers: {{ 'Content-Type': 'application/json' }},
                    }});
                  }}
                  if (method === 'DELETE') {{
                    window.__assistantHistoryDeleteRequested = true;
                    return new Response(JSON.stringify({{ status: 'cleared' }}), {{
                      status: 200,
                      headers: {{ 'Content-Type': 'application/json' }},
                    }});
                  }}
                }}
                return originalFetch(input, init);
              }};
            }})();
            """
        )

        page.reload(wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("input.chat-input", state="visible", timeout=20_000)
        page.wait_for_function("() => window.__assistantHistoryRequested === true", timeout=10_000)

        clear_button = page.locator("button:has-text('Clear')")
        clear_button.click()
        page.wait_for_function("() => window.__assistantHistoryDeleteRequested === true", timeout=10_000)
        page.wait_for_function("() => document.querySelectorAll('div.chat-message.assistant').length === 0", timeout=10_000)

        page.evaluate("() => window.__releaseAssistantHistory()")
        page.wait_for_timeout(1000)

        assert page.locator("div.chat-message.assistant", has_text=stale_text).count() == 0
        assert page.locator("div.chat-message.system").count() == 1
        take_screenshot(page, "agent_run_clear_history_race", screenshot_dir)
