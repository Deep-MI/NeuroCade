"""Test gui helpers behavior for NeuroCade."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from conftest import DEMO_CASE_ID, fresh_processed_case_data

if TYPE_CHECKING:
    from playwright.sync_api import Page


GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8005")
DEFAULT_CASE_ID = os.environ.get("GUI_CASE_ID", DEMO_CASE_ID)
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
DEFAULT_STORAGE_STATE_PATH = Path(
    os.environ.get(
        "PLAYWRIGHT_STORAGE_STATE",
        Path(__file__).resolve().parent.parent / "playwright" / ".clerk" / "user.json",
    )
)


def infer_case_name(filename: str) -> str:
    """Return a default case name derived from an uploaded MRI filename."""
    lower = filename.lower()
    if lower.endswith(".nii.gz"):
        base = filename[:-7]
    elif lower.endswith(".nii") or lower.endswith(".mgz"):
        base = filename.rsplit(".", 1)[0]
    else:
        base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return slug_name(base)


def slug_name(value: str) -> str:
    """Return a slug-compatible test name."""
    candidate = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    candidate = candidate[:64].rstrip("-")
    return candidate or "case"


def routed_case_id(page: "Page") -> str:
    """Return the API case ID represented by the current workspace case route."""
    parts = page.url.split("?", 1)[0].rstrip("/").split("/")
    try:
        workspace_index = parts.index("workspaces")
        workspace_id = parts[workspace_index + 1]
        case_slug = parts[workspace_index + 3]
    except (ValueError, IndexError) as exc:
        raise AssertionError(f"Expected workspace case route, got {page.url}") from exc
    return f"{workspace_id}__{case_slug}"


def upload_mri(
    page: "Page",
    filepath: str,
    case_name: str | None = None,
    trigger_selector: str = "button:has-text('Choose MRI File')",
    destination: str = "new_case",
) -> None:
    """Upload an MRI file and confirm the upload modal."""
    abs_path = os.path.abspath(filepath)
    assert os.path.exists(abs_path), f"File not found: {abs_path}"
    assert destination in {"new_case", "add_to_case"}, f"Unsupported upload destination: {destination}"

    page.click(trigger_selector)
    dropzone = page.locator("[data-testid='upload-file-dropzone']")
    dropzone.wait_for(state="visible", timeout=10_000)
    with page.expect_file_chooser() as fc_info:
        dropzone.click()
    file_chooser = fc_info.value
    file_chooser.set_files(abs_path)
    page.locator("[data-testid='upload-case-name-input']").wait_for(state="visible", timeout=10_000)
    target_name = case_name or infer_case_name(Path(abs_path).name)
    page.locator("[data-testid='upload-case-name-input']").fill(target_name)
    if destination == "add_to_case":
        page.click("[data-testid='confirm-add-to-case']")
    else:
        page.click("[data-testid='confirm-upload-case']")
    page.locator("[data-testid='upload-case-name-input']").wait_for(state="hidden", timeout=30_000)
    page.wait_for_url("**/workspaces/*/cases/*", timeout=30_000)
    page.wait_for_selector("input.chat-input", state="visible", timeout=20_000)
    page.wait_for_selector("button:has-text('Choose MRI File')", state="visible", timeout=20_000)
    time.sleep(1)


def get_auth_headers() -> dict[str, str]:
    """Build Authorization headers from the saved Clerk Playwright storage state."""
    explicit_token = os.environ.get("APP_AUTH_TOKEN", "").strip()
    if explicit_token:
        return {"Authorization": f"Bearer {explicit_token}"}
    if not DEFAULT_STORAGE_STATE_PATH.exists():
        return {}
    try:
        state = json.loads(DEFAULT_STORAGE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    for cookie in state.get("cookies", []):
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name == "__session" and value and not _jwt_is_expired(value):
            return {"Authorization": f"Bearer {value}"}
    return {}


def _jwt_is_expired(token: str) -> bool:
    """Return whether a JWT exp claim is in the past."""
    parts = token.split(".")
    if len(parts) < 2:
        return False
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return exp <= time.time()


def send_chat_message(page: "Page", message: str, timeout: int = 120_000) -> str:
    """Send a chat message and return the latest assistant response text."""
    chat_input = page.locator("input.chat-input")
    chat_input.fill(message)
    page.click("button:has-text('Send')")

    page.wait_for_selector(
        "div.chat-message.info:has-text('Assistant is')",
        state="visible",
        timeout=10_000,
    )
    page.wait_for_selector(
        "div.chat-message.info:has-text('Assistant is')",
        state="hidden",
        timeout=timeout,
    )
    time.sleep(0.5)

    assistant_msgs = page.locator("div.chat-message.assistant")
    count = assistant_msgs.count()
    if count == 0:
        return ""
    return assistant_msgs.nth(count - 1).inner_text()


def get_current_position(page: "Page") -> dict:
    """Read the current cursor/label display from the viewer toolbar."""
    result: dict[str, str | None] = {"voxel_text": None, "label_index": None, "label_name": None}

    coords_el = page.locator("div.coordinates-display div.coordinates span").last
    if coords_el.is_visible():
        result["voxel_text"] = coords_el.inner_text()

    label_index_el = page.locator("div.coordinates-display div:has-text('Index:')").first
    if label_index_el.is_visible():
        result["label_index"] = label_index_el.inner_text()

    label_name_el = page.locator("div.coordinates-display .text-sm.font-black").first
    if label_name_el.is_visible():
        result["label_name"] = label_name_el.inner_text()

    return result


def load_processed_case(page: "Page") -> str:
    """Navigate to a reproducible processed case copied into a fresh workspace."""
    processed_case = fresh_processed_case_data()
    workspace_id = processed_case["workspace_id"]
    case_id = processed_case["case_id"]
    prefix = f"{workspace_id}__"
    if not case_id.startswith(prefix):
        raise ValueError("Processed test case id must use the canonical workspace-prefixed format")
    case_slug = case_id[len(prefix):]

    page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases/{case_slug}", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_url(f"**/workspaces/{workspace_id}/cases/{case_slug}", timeout=15_000)
    time.sleep(3)
    return processed_case.get("title") or case_id


def take_screenshot(page: "Page", name: str, screenshot_dir: Path = SCREENSHOT_DIR) -> Path:
    """Take a full-page screenshot and save it."""
    screenshot_dir.mkdir(exist_ok=True)
    path = screenshot_dir / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path
