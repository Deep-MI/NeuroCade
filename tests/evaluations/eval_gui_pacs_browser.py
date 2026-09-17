"""Browser acceptance flow against the built SPA and synthetic API responses."""

import json
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.gui
@pytest.mark.parametrize("scenario", ["import", "failed_run", "case_switch"])
def test_pacs_search_select_and_duplicate(tmp_path, scenario):
    dist = Path(__file__).resolve().parents[2] / "client/dist"
    if not (dist / "index.html").is_file():
        pytest.skip("Build the client before running the PACS browser test")

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/workspaces"):
                self.path = "/index.html"
            super().do_GET()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            submitted = []
            held = {}
            counts = {}

            def route_api(route):
                path = route.request.url.split("/api/app/")[1].split("?")[0]
                status = 200
                payload = []
                counts[path] = counts.get(path, 0) + 1
                if scenario == "case_switch" and path in {"cases/case/runs", "cases/case/logs", "cases/case/artifacts"} and counts[path] > 1 and path not in held:
                    held[path] = route
                    return
                if path == "frontend-config":
                    payload = {"local_auth_enabled": True}
                elif path == "session":
                    payload = {"user": {"id": "user", "email": "research@example.org", "full_name": "Researcher"}, "features": {}, "default_workspace_id": "workspace", "workspaces": [{"id": "workspace", "name": "research", "role": "owner", "kind": "shared", "is_default": True, "case_count": 0}]}
                elif path == "pacs/status":
                    payload = {"enabled": True}
                elif path.startswith("pacs/cases/"):
                    payload = None
                elif path == "cases":
                    payload = [{"id": "case", "workspace_id": "workspace", "title": "QA case", "status": "failed"}]
                elif path == "cases/case/runs":
                    payload = [{"id": "run", "status": "failed", "run_type": "fastsurfer", "error_message": "PACS input sequence is incompatible or unverified for this workflow", "error_code": "pacs_sequence_incompatible"}]
                    if scenario == "case_switch":
                        payload = [{"id": "run", "status": "running", "run_type": "fastsurfer"}]
                elif path == "cases/next/runs":
                    payload = [{"id": "next-run", "status": "failed", "run_type": "fastsurfer", "error_message": "Current case failure"}]
                elif path == "cases/next/logs":
                    payload = {"logs": "Current case logs"}
                elif path == "cases/case/logs":
                    payload = {"logs": ""}
                elif path == "cases/case/outputs":
                    payload = {"outputs": []}
                elif path == "pacs/studies/search":
                    payload = [{"study_uid": "1.2", "patient_name": "Synthetic Patient", "patient_id": "TEST001", "birth_date": "19800101", "study_date": "20260901", "description": "Brain MRI"}]
                elif path == "pacs/studies/1.2/series":
                    payload = [{"series_uid": "1.2.1", "description": "T1 volume", "eligible": True, "state": "queued"}, {"series_uid": "1.2.2", "description": "Report", "eligible": False, "reason": "Unsupported DICOM object type", "state": "queued"}]
                elif path == "pacs/imports" and route.request.method == "POST":
                    request = route.request.post_data_json
                    submitted.append(request)
                    if not request["confirm_duplicate"]:
                        status, payload = 409, {"detail": {"case_ids": ["existing"]}}
                    else:
                        status, payload = 202, {"id": "import", "case_id": "new-case", "state": "queued", "series": []}
                route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

            page.route("**/api/app/**", route_api)
            if scenario == "case_switch":
                page.goto(f"http://127.0.0.1:{server.server_port}/workspaces/workspace/cases/case")
                page.get_by_role("button", name="Terminal", exact=True).click()
                deadline = time.monotonic() + 25
                while len(held) < 3 and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert len(held) == 3, "Expected delayed status, log, and output requests"
                # Client-side navigation preserves the controller and pending fetches.
                page.evaluate("history.pushState(null, '', '/workspaces/workspace/cases/next'); dispatchEvent(new PopStateEvent('popstate'))")
                expect(page.get_by_test_id("terminal-job-status")).to_contain_text("Current case failure")
                for path, route in held.items():
                    payload = [{"id": "run", "status": "failed", "error_message": "STALE case failure"}] if path.endswith("runs") else {"logs": "STALE case logs"} if path.endswith("logs") else [{"id": "old-volume", "name": "STALE volume.nii", "kind": "volume", "download_path": "/api/app/stale-volume", "metadata": {"volume_role": "intensity"}}]
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
                page.wait_for_timeout(500)
                expect(page.get_by_test_id("terminal-job-status")).to_contain_text("Current case failure")
                expect(page.locator("body")).not_to_contain_text("STALE")
                page.screenshot(path=str(tmp_path / "case-switch.png"))
                browser.close()
                return
            if scenario == "failed_run":
                page.goto(f"http://127.0.0.1:{server.server_port}/workspaces/workspace/cases/case")
                page.get_by_role("button", name="Terminal", exact=True).click()
                expect(page.get_by_test_id("terminal-job-status")).to_contain_text("PACS input sequence is incompatible or unverified")
                expect(page.get_by_test_id("terminal-job-status")).to_contain_text("Choose a native, noncontrast structural T1")
                expect(page.get_by_test_id("terminal-content")).not_to_contain_text("No analysis run yet")
                page.reload()
                page.get_by_role("button", name="Terminal", exact=True).click()
                expect(page.get_by_test_id("terminal-job-status")).to_contain_text("PACS input sequence is incompatible or unverified")
                browser.close()
                return
            page.goto(f"http://127.0.0.1:{server.server_port}/workspaces/workspace/cases")
            page.get_by_role("button", name="Import from PACS").click()
            page.get_by_label("Exact patient ID").fill("TEST001")
            page.get_by_role("button", name="Search", exact=True).click()
            page.get_by_role("button", name="Synthetic Patient", exact=False).click()
            expect(page.get_by_role("checkbox").nth(0)).to_be_checked()
            expect(page.get_by_role("checkbox").nth(1)).to_be_disabled()
            page.get_by_role("button", name="Import 1 series as one case").click()
            expect(page.get_by_text("This study was already imported.")).to_be_visible()
            page.screenshot(path=str(tmp_path / "pacs-import.png"))
            page.get_by_role("button", name="Confirm separate copy").click()
            expect(page.get_by_text("Open research copy", exact=True)).to_be_visible()
            assert submitted[-1]["series_uids"] == ["1.2.1"]
            assert submitted[0]["submission_key"] == submitted[1]["submission_key"]
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
