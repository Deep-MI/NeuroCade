"""
GUI E2E test for uploading a public DICOM ZIP fixture.

The fixture is intentionally not committed. Download it before running:

    mkdir -p /tmp/fastsurfer-public-dicom
    curl -L -o /tmp/fastsurfer-public-dicom/DICOM.zip \
      "https://zenodo.org/records/16956/files/DICOM.zip?download=1"

Usage:
    TEST_DICOM_ZIP=/tmp/fastsurfer-public-dicom/DICOM.zip pytest tests/test_gui_dicom_upload.py -v
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest

from gui_helpers import GATEWAY_URL, get_auth_headers, routed_case_id, slug_name, take_screenshot


pytest_plugins = ["conftest_gui"]

PUBLIC_DICOM_URL = "https://zenodo.org/records/16956/files/DICOM.zip?download=1"
PUBLIC_DICOM_MD5 = "e5cbd0bca91f1787d057b0eac2572bde"
DEFAULT_PUBLIC_DICOM_ZIP = Path("/tmp/fastsurfer-public-dicom/DICOM.zip")


def _file_md5(path: Path) -> str:
    """Return the hexadecimal MD5 digest for a file."""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture()
def public_head_dicom_zip() -> Path:
    """Return the configured public DICOM ZIP fixture, skipping if it is unavailable."""
    path = Path(os.environ.get("TEST_DICOM_ZIP", DEFAULT_PUBLIC_DICOM_ZIP)).expanduser()
    if not path.exists():
        pytest.skip(
            f"Public DICOM fixture not found at {path}. Download it with: "
            f"curl -L -o {DEFAULT_PUBLIC_DICOM_ZIP} '{PUBLIC_DICOM_URL}'"
        )
    if path.resolve() == DEFAULT_PUBLIC_DICOM_ZIP and _file_md5(path) != PUBLIC_DICOM_MD5:
        pytest.fail(f"Unexpected checksum for {path}; expected Zenodo md5 {PUBLIC_DICOM_MD5}")
    return path


def _create_workspace(page, name: str) -> dict:
    """Create a workspace through the browser API context and return its payload."""
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


def test_public_head_dicom_zip_upload_converts_and_selects_t1_input_candidate(page, screenshot_dir, public_head_dicom_zip):
    workspace = _create_workspace(page, f"dicom-upload-{uuid4().hex[:8]}")
    workspace_id = workspace["id"]
    case_name = f"zenodo-head-dicom-{uuid4().hex[:6]}"

    page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_url(f"**/workspaces/{workspace_id}/cases", timeout=15_000)

    upload_tile = page.locator("button:has-text('Upload Case')").first
    upload_tile.wait_for(state="visible", timeout=10_000)
    assert upload_tile.locator("text=DICOM ZIP archives").is_visible()

    upload_tile.click()
    dropzone = page.locator("[data-testid='upload-file-dropzone']")
    dropzone.wait_for(state="visible", timeout=10_000)
    with page.expect_file_chooser() as chooser_info:
        dropzone.click()
    chooser_info.value.set_files(str(public_head_dicom_zip))

    name_input = page.locator("[data-testid='upload-case-name-input']")
    name_input.wait_for(state="visible", timeout=10_000)
    assert name_input.input_value() == "dicom"
    name_input.fill(case_name)

    with page.expect_response(
        lambda response: response.request.method == "POST" and "/api/app/cases" in response.url,
        timeout=120_000,
    ) as upload_response:
        page.click("[data-testid='confirm-upload-case']")
    response = upload_response.value
    assert response.ok, f"DICOM ZIP upload failed with {response.status}: {response.text()}"

    page.wait_for_url(f"**/workspaces/{workspace_id}/cases/*", timeout=30_000)
    page.wait_for_selector("button:has-text('Run FastSurfer Analysis')", state="visible", timeout=30_000)
    page.locator("div.layer-item", has_text=f"{case_name}.nii.gz").wait_for(state="visible", timeout=30_000)

    case_id = routed_case_id(page)
    artifacts = page.evaluate(
        """async (caseId) => {
          const response = await fetch(`/api/app/cases/${encodeURIComponent(caseId)}/artifacts`);
          if (!response.ok) {
            return { ok: false, status: response.status, detail: await response.text() };
          }
          return { ok: true, artifacts: await response.json() };
        }""",
        case_id,
    )
    assert artifacts.get("ok"), f"Failed to fetch artifacts for {case_id}: {artifacts}"

    uploads = [artifact for artifact in artifacts["artifacts"] if artifact["kind"] == "volume"]
    selected_input = next((artifact for artifact in uploads if artifact["metadata"].get("dicom_selected_input_candidate")), None)

    assert len(uploads) >= 5
    assert selected_input is not None
    assert selected_input["name"] == f"{case_name}.nii.gz"
    assert selected_input["metadata"]["dicom_converted"] is True
    assert selected_input["metadata"]["dicom_input_selection_reason"] == "structural series hint"
    assert selected_input["metadata"]["original_converted_name"] == "T1W_FFE_301.nii.gz"
    assert selected_input["size_bytes"] > 1_000_000

    with page.expect_response(
        lambda response: response.request.method == "POST" and "/api/app/runs" in response.url,
        timeout=120_000,
    ) as run_response:
        page.click("button:has-text('Run FastSurfer Analysis')")
        page.click("button:has-text('Begin Run')")
    run_result = run_response.value
    assert run_result.ok, f"FastSurfer run start failed with {run_result.status}: {run_result.text()}"
    run_payload = run_result.json()
    assert run_payload["case_id"] == case_id
    assert run_payload["status"] in {"queued", "running"}

    page.locator("button:has-text('Cancel Analysis')").wait_for(state="visible", timeout=30_000)
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.request.method == "POST" and f"/api/app/cases/{case_id}/cancel" in response.url,
        timeout=30_000,
    ) as cancel_response:
        page.click("button:has-text('Cancel Analysis')")
    cancel_result = cancel_response.value
    assert cancel_result.ok, f"FastSurfer cancel failed with {cancel_result.status}: {cancel_result.text()}"

    take_screenshot(page, "dicom_zip_upload_converted", screenshot_dir)
