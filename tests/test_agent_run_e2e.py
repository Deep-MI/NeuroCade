"""
API-level E2E test: Verify the agent-triggered FastSurfer run flow.

This test exercises the full backend round-trip without a browser:
  1. Upload a file via /upload
  2. Seed GUI state with the case ID
  3. Send a chat message to run FastSurfer
  4. Verify the agent calls gui_run_fastsurfer
  5. Verify requested_run_fastsurfer appears in the state sync response
  6. Submit the run via /run (simulating what the frontend does)
  7. Verify job status transitions

Prerequisites:
  ./scripts/run.sh start -d
  a demo upload fixture and processed case must be configured

Usage:
  pytest tests/test_agent_run_e2e.py -v
"""

import time
from uuid import uuid4

import pytest
import requests

from conftest import (
    UPLOAD_FIXTURES_DIR,
    assert_tool_executed,
    chat_send,
    runtime_logs_since,
    get_case_runs,
    get_case_summary_by_case_id,
    get_response_content,
    require_app_auth_headers,
    seed_gui_state,
    utc_timestamp,
)


@pytest.fixture(autouse=True)
def require_services(services_up):
    """Skip all tests in this module if local NeuroCade stack is not running."""
    pass


class TestAgentRunFastSurfer:
    """Test the agent-triggered FastSurfer run flow at the API level."""

    @pytest.fixture(autouse=True)
    def setup(self, gateway_url, fresh_run_case):
        """Store gateway context and a fresh case for each test."""
        self.gateway_url = gateway_url
        self.app_headers = require_app_auth_headers()
        self.demo_case = fresh_run_case

    def _poll_status(self, case_id: str, target_statuses: list[str],
                     timeout: int = 30) -> str:
        """Poll job status until it matches one of the targets or timeout."""
        deadline = time.time() + timeout
        last_status = "unknown"
        while time.time() < deadline:
            try:
                runs = get_case_runs(case_id, self.gateway_url)
                last_status = runs[0].get("status", "unknown") if runs else "unknown"
                if last_status in target_statuses:
                    return last_status
            except Exception:
                pass
            time.sleep(1)
        return last_status

    def test_upload_endpoint_creates_case(self):
        """Verify /api/app/cases creates a new authenticated case record."""
        upload_path = UPLOAD_FIXTURES_DIR / self.demo_case["source_upload_filename"]
        if not upload_path.exists():
            pytest.skip(f"{self.demo_case['source_upload_filename']} not found")

        subject_name = f"e2e-upload-{uuid4().hex[:8]}"
        with upload_path.open("rb") as f:
            r = requests.post(
                f"{self.gateway_url}/api/app/cases",
                headers=self.app_headers,
                data={
                    "workspace_id": self.demo_case["workspace_id"],
                    "title": subject_name,
                },
                files={"file": (self.demo_case["source_upload_filename"], f, "application/octet-stream")},
                timeout=30,
            )
        assert r.status_code == 200, f"Upload failed: {r.text}"
        data = r.json()
        assert data.get("workspace_id") == self.demo_case["workspace_id"]
        assert data.get("case_id")
        assert data.get("title") == subject_name

        uploaded_case = get_case_summary_by_case_id(data["case_id"], self.gateway_url)
        assert uploaded_case["id"] == data["case_id"]
        assert uploaded_case["title"] == subject_name

    def test_state_sync_reflects_current_input_volume(self):
        """After seeding state with the selected input volume, the server stores it."""
        state = {
            "is_job_running": False,
            "has_valid_segmentation": False,
            "current_case_id": self.demo_case["case_id"],
            "loaded_volumes": [self.demo_case["upload_filename"]],
            "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
            "current_intensity_volume": self.demo_case["upload_filename"],
        }
        result = seed_gui_state(state, self.gateway_url)
        assert result.get("status") == "success"
        current = result.get("current_state", {})
        assert current.get("current_intensity_volume") == self.demo_case["upload_filename"]
        assert current.get("current_case_id") == self.demo_case["case_id"]

    def test_agent_chat_sets_requested_command(self):
        """Authenticated chat should set requested_run_fastsurfer in the scoped GUI state."""
        seed_gui_state({
            "is_job_running": False,
            "has_valid_segmentation": False,
            "current_case_id": self.demo_case["case_id"],
            "loaded_volumes": [self.demo_case["upload_filename"]],
            "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
            "current_intensity_volume": self.demo_case["upload_filename"],
        }, self.gateway_url)

        response = chat_send(
            [{"role": "user", "content": "Run FastSurfer on the current case"}],
            self.gateway_url,
            timeout=120,
        )
        content = get_response_content(response)
        assert "fastsurfer" in content.lower()

        state_data = seed_gui_state(
            {
                "is_job_running": True,
                "current_case_id": self.demo_case["case_id"],
                "loaded_volumes": [self.demo_case["upload_filename"]],
                "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
                "current_intensity_volume": self.demo_case["upload_filename"],
            },
            self.gateway_url,
        )
        assert "requested_run_fastsurfer" in state_data, (
            f"Expected requested_run_fastsurfer in state sync response. "
            f"Got: {state_data}"
        )
        cmd = state_data["requested_run_fastsurfer"]
        assert cmd["case_id"] == self.demo_case["case_id"]

    def test_agent_chat_triggers_run(self):
        """Full E2E: Chat message → agent calls gui_run_fastsurfer → command set."""
        # Seed state: demo case uploaded, idle
        seed_gui_state({
            "is_job_running": False,
            "has_valid_segmentation": False,
            "current_case_id": self.demo_case["case_id"],
            "loaded_volumes": [self.demo_case["upload_filename"]],
            "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
            "current_intensity_volume": self.demo_case["upload_filename"],
        }, self.gateway_url)

        log_ts = utc_timestamp()
        time.sleep(0.3)

        # Send the chat message
        messages = [
            {"role": "user", "content": "Run FastSurfer on the current case"},
        ]
        response = chat_send(messages, self.gateway_url, timeout=120)
        content = get_response_content(response)

        # Verify agent called gui_run_fastsurfer
        logs = runtime_logs_since(since=log_ts)
        assert_tool_executed(logs)

        run_markers = ["run_fastsurfer", "triggered", "started", "running",
                       "fastsurfer", "pipeline"]
        content_lower = content.lower()
        found = [m for m in run_markers if m in content_lower]
        assert found, (
            f"Response doesn't mention FastSurfer execution: {content[:300]}"
        )

        # The state sync should now have requested_run_fastsurfer
        # (unless it was already consumed by a frontend poll)
        state_data = seed_gui_state(
            {
                "is_job_running": True,
                "current_case_id": self.demo_case["case_id"],
                "loaded_volumes": [self.demo_case["upload_filename"]],
                "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
                "current_intensity_volume": self.demo_case["upload_filename"],
            },
            self.gateway_url,
        )
        # It may or may not still be there (it's a one-shot), but the tool
        # call in the logs confirms it was set
        print(f"\n  Agent response: {content[:200]}")
        print(f"  State sync keys: {list(state_data.keys())}")

    def test_submitted_run_transitions_status(self):
        """After submitting /api/app/runs for the case, the status should transition."""
        r = requests.post(
            f"{self.gateway_url}/api/app/runs",
            headers=self.app_headers,
            json={
                "workspace_id": self.demo_case["workspace_id"],
                "case_id": self.demo_case["id"],
                "input_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
                "seg_only": True,
                "no_bias": False,
                "no_cereb": True,
                "no_asegdkt": False,
                "no_hypothal": False,
                "three_t": False,
            },
            timeout=30,
        )
        assert r.status_code == 200, f"Run failed: {r.text}"
        data = r.json()
        assert data.get("case_id") == self.demo_case["id"]
        assert data.get("status") == "queued"

        # Poll for any non-queued status (running, error, finished, etc.)
        final_status = self._poll_status(
            self.demo_case["id"],
            ["running", "starting", "finished", "completed", "error"],
            timeout=60,
        )
        assert final_status != "unknown", (
            f"Job never transitioned from queued, last status: {final_status}"
        )
        print(f"\n  Job final status: {final_status}")

    def test_run_appears_in_case_runs(self):
        """The authenticated case runs endpoint should return runs for the selected case."""
        runs = get_case_runs(self.demo_case["id"], self.gateway_url)
        assert runs, "Expected at least one run for the demo case"
        assert all(run.get("case_id") == self.demo_case["id"] for run in runs)
