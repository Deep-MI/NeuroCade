"""
Playwright fixtures and helpers for NeuroCade GUI end-to-end tests.

These tests drive a real Chromium browser against the running local stack.

Prerequisites:
    ./scripts/run.sh start -d
    pip install playwright && playwright install chromium

Usage:
    pytest tests/test_gui_*.py -v
    HEADED=1 pytest tests/test_gui_*.py -v   # watch the browser

Note: This module is NOT auto-discovered as a conftest. GUI test files use
`pytest_plugins = ["conftest_gui"]` to register these fixtures.
"""

from __future__ import annotations

import importlib
import os
import shutil

import pytest
import requests
from gui_helpers import DEFAULT_CASE_ID, DEFAULT_STORAGE_STATE_PATH, GATEWAY_URL, SCREENSHOT_DIR
from gui_helpers import get_auth_headers

# Lazy-import playwright so non-GUI tests don't fail when it's not installed
try:
        _sync_api = importlib.import_module("playwright.sync_api")
except ImportError:
        HAS_PLAYWRIGHT = False
        sync_playwright = None  # type: ignore[assignment]
else:
        HAS_PLAYWRIGHT = True
        sync_playwright = _sync_api.sync_playwright


@pytest.fixture(scope="session")
def _check_playwright():
    """Skip the entire session if Playwright is not installed."""
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed. Run: pip install playwright && playwright install chromium")


@pytest.fixture(scope="session")
def browser(_check_playwright):
    """Launch a Chromium browser (session-scoped, reused across all GUI tests)."""
    assert sync_playwright is not None
    executable_path = (
        os.environ.get("CHROMIUM_EXECUTABLE_PATH")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    launch_options: dict[str, object] = {
        "headless": os.environ.get("HEADED", "").lower() not in ("1", "true"),
    }
    if executable_path:
        launch_options["executable_path"] = executable_path
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def screenshot_dir():
    """Create and return the screenshots directory."""
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    return SCREENSHOT_DIR


@pytest.fixture()
def page(services_up, browser):
    """Create a fresh browser page for each test, navigated to a workspace."""
    context_kwargs = {
        "viewport": {"width": 1440, "height": 900},
        "ignore_https_errors": True,
    }
    if DEFAULT_STORAGE_STATE_PATH.exists():
        context_kwargs["storage_state"] = str(DEFAULT_STORAGE_STATE_PATH)
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    target_url = f"{GATEWAY_URL}/"
    auth_headers = get_auth_headers()
    target_case_id = DEFAULT_CASE_ID

    if auth_headers:
        try:
            session_response = requests.get(
                f"{GATEWAY_URL}/api/app/session",
                headers=auth_headers,
                timeout=10,
            )
            session_response.raise_for_status()
            session_payload = session_response.json()
            cases_response = requests.get(
                f"{GATEWAY_URL}/api/app/cases",
                headers=auth_headers,
                timeout=10,
            )
            cases_response.raise_for_status()
            raw_cases_payload = cases_response.json()
            case_payload = raw_cases_payload if isinstance(raw_cases_payload, list) else raw_cases_payload.get("cases", [])
            target_case = next(
                (case for case in case_payload if case.get("id") == DEFAULT_CASE_ID),
                None,
            )
            if target_case is None and case_payload:
                target_case = case_payload[0]
            if target_case is not None:
                workspace_id_value = target_case.get("workspace_id") or session_payload.get("active_workspace_id") or session_payload.get("default_workspace_id")
                target_case_id = str(target_case.get("id") or DEFAULT_CASE_ID)
                workspace_id = str(workspace_id_value) if workspace_id_value else ""
                if workspace_id:
                    prefix = f"{workspace_id}__"
                    if target_case_id.startswith(prefix):
                        case_slug = target_case_id[len(prefix):]
                        target_url = f"{GATEWAY_URL}/workspaces/{workspace_id}/cases/{case_slug}"
                    else:
                        target_url = f"{GATEWAY_URL}/workspaces/{workspace_id}/cases"
                else:
                    target_url = f"{GATEWAY_URL}/"
            else:
                workspace_id = session_payload.get("active_workspace_id") or session_payload.get("default_workspace_id")
                if workspace_id:
                    target_url = f"{GATEWAY_URL}/workspaces/{workspace_id}/cases"
        except requests.RequestException:
            pass

    page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_function(
        """() => {
          const hasCaseCard = Array.from(document.querySelectorAll('a,button')).some((el) => el.textContent?.includes('Case ID:'));
          const hasUploadButton = Array.from(document.querySelectorAll('button')).some((el) => el.textContent?.includes('Choose MRI File'));
          const hasChatInput = !!document.querySelector('input.chat-input');
          const hasWorkspaceTitle = Array.from(document.querySelectorAll('h1')).some((el) => el.textContent?.includes('NeuroCade Workspaces'));
          const hasSignIn = Array.from(document.querySelectorAll('h1')).some((el) => el.textContent?.includes('NeuroCade Sign In'));
          return hasCaseCard || hasUploadButton || hasChatInput || hasWorkspaceTitle || hasSignIn;
        }""",
        timeout=30_000,
    )
    if "/sign-in" in page.url or "NeuroCade Sign In" in page.locator("body").inner_text(timeout=5_000):
        pytest.skip("Saved Clerk Playwright storage state is not authenticated; sign in and refresh PLAYWRIGHT_STORAGE_STATE to run GUI tests.")
    if "/cases/" not in page.url.rstrip("/"):
        case_card = page.locator(f"a:has-text('Case ID: {target_case_id}'), button:has-text('Case ID: {target_case_id}')").first
        if case_card.count() == 0:
            case_card = page.locator("a:has-text('Case ID:'), button:has-text('Case ID:')").first
        case_card.wait_for(state="visible", timeout=20_000)
        case_card.click()
    page.wait_for_url("**/workspaces/*/cases/*", timeout=30_000)
    page.wait_for_function(
        """() => !!document.querySelector('input.chat-input')
          || Array.from(document.querySelectorAll('button')).some((el) => el.textContent?.includes('Choose MRI File'))""",
        timeout=20_000,
    )
    yield page
    context.close()
