"""
End-to-end test: Run FastSurfer via chat, then cancel through the backend API.

Flow:
  1. Seed GUI state for a real demo upload (idle, with uploaded file)
  2. Send "Run FastSurfer on the current case" via chat API
  3. Verify the agent calls gui_run_fastsurfer (from proxy logs)
  4. Drain the typed run_fastsurfer command from GUI state sync
  5. Submit /run using that command (mimics the frontend handoff)
  6. Verify the job reaches a cancellable state
  7. POST /cancel/<demo-case>
  8. Verify status becomes 'canceled'

Prerequisites:
  ./scripts/run.sh start -d
  At least one MRI fixture must be available via NEUROCADE_UPLOAD_FIXTURES_DIR

Usage:
  pytest tests/test_fastsurfer_run_e2e.py -v
"""

import time

import pytest
import requests
from conftest import (
    assert_no_text_explanation,
    assert_tool_executed,
    chat_send,
    get_case_runs,
    get_response_content,
    require_app_auth_headers,
    runtime_logs_since,
    seed_gui_state,
    utc_timestamp,
)


@pytest.fixture(autouse=True)
def require_services(services_up):
    """Skip all tests in this module if local NeuroCade stack is not running."""
    pass


class TestFastSurferRunCancel:
    """Run FastSurfer via chat and cancel the job after 20 seconds."""

    @pytest.fixture(autouse=True)
    def setup(self, gateway_url, fresh_run_case):
        """Capture gateway and case details for the FastSurfer run."""
        self.gateway_url = gateway_url
        self.demo_run_case_id = fresh_run_case["case_id"]
        self.demo_run_upload_filename = fresh_run_case["upload_filename"]
        self.app_headers = require_app_auth_headers()
        self.demo_case = fresh_run_case

    def _poll_status(self, case_id: str, target_statuses: str | tuple[str, ...], timeout: int = 30) -> str:
        """Poll job status until it matches one of the targets or timeout."""
        deadline = time.time() + timeout
        last_status = "unknown"
        expected = {target_statuses} if isinstance(target_statuses, str) else set(target_statuses)
        while time.time() < deadline:
            try:
                runs = get_case_runs(case_id, self.gateway_url)
                last_status = runs[0].get("status", "unknown") if runs else "unknown"
                if last_status in expected:
                    return last_status
            except Exception:
                pass
            time.sleep(2)
        return last_status

    def test_run_and_cancel_fastsurfer(self):
        """Full E2E: chat triggers FastSurfer, then we cancel after 20s."""
        # Step 1: Seed GUI state — idle, ready to run
        result = seed_gui_state(
            {
                "is_job_running": False,
                "current_case_id": self.demo_run_case_id,
                "layers": [{
                    "id": "intensity:input",
                    "filename": self.demo_run_upload_filename,
                    "type": "intensity",
                    "role": "intensity",
                    "visible": True,
                }],
                "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
                "current_intensity_volume": self.demo_run_upload_filename,
            },
            self.gateway_url,
        )
        assert "current_state" in result

        # Step 2: Send the chat message
        log_ts = utc_timestamp()
        time.sleep(0.3)

        messages = [
            {"role": "user", "content": "Run FastSurfer on the current case"},
        ]
        response = chat_send(messages, self.gateway_url, timeout=120)
        content = get_response_content(response)

        # Step 3: Verify agent called a tool, not a text explanation
        logs = runtime_logs_since(since=log_ts)
        assert_tool_executed(logs)
        assert_no_text_explanation(content)

        # Check the tool was gui_run_fastsurfer or that the response mentions running
        run_markers = ["run_fastsurfer", "triggered", "started", "running", "fastsurfer"]
        content_lower = content.lower()
        found = [m for m in run_markers if m in content_lower]
        assert found, (
            f"Response does not mention FastSurfer execution: {content[:300]}"
        )

        # Step 4: Drain the frontend one-shot command the same way the routed UI does.
        state_data = seed_gui_state(
            {
                "is_job_running": True,
                "current_case_id": self.demo_run_case_id,
                "layers": [{
                    "id": "intensity:input",
                    "filename": self.demo_run_upload_filename,
                    "type": "intensity",
                    "role": "intensity",
                    "visible": True,
                }],
                "current_intensity_artifact_id": self.demo_case["gui_state"]["current_intensity_artifact_id"],
                "current_intensity_volume": self.demo_run_upload_filename,
            },
            self.gateway_url,
        )
        commands = [
            command for command in state_data.get("commands", [])
            if command.get("type") == "run_fastsurfer"
        ]
        assert commands, f"Expected run_fastsurfer command in GUI sync response, got: {state_data}"
        run_cmd = commands[0]["payload"]

        # Step 5: Submit the run explicitly to mimic the frontend handoff.
        run_r = requests.post(
            f"{self.gateway_url}/api/app/runs",
            headers=self.app_headers,
            json={
                "workspace_id": self.demo_case["workspace_id"],
                "case_id": self.demo_case["id"],
                "input_artifact_id": run_cmd.get("input_artifact_id"),
                "seg_only": bool(run_cmd.get("seg_only", True)),
                "no_bias": False,
                "no_cereb": True,
                "no_asegdkt": False,
                "no_hypothal": False,
                "three_t": False,
            },
            timeout=30,
        )
        run_r.raise_for_status()
        run_data = run_r.json()
        assert run_data.get("case_id") == self.demo_case["id"]
        assert run_data.get("status") == "queued"

        # Step 6: Poll for a cancellable state.
        status = self._poll_status(
            self.demo_case["id"],
            ("queued", "running", "starting"),
            timeout=15,
        )
        assert status in ("running", "starting", "queued"), (
            f"Expected job to be running/starting/queued, got: {status}"
        )

        # Step 7: Cancel the job before the worker can transition it elsewhere.
        cancel_url = f"{self.gateway_url}/api/app/cases/{self.demo_case['id']}/cancel"
        r = requests.post(cancel_url, headers=self.app_headers, timeout=10)
        r.raise_for_status()
        cancel_data = r.json()
        assert cancel_data.get("status") == "canceled"

        # Step 8: Verify final status
        final_status = self._poll_status(self.demo_case["id"], "canceled", timeout=15)
        assert final_status == "canceled", (
            f"Expected 'canceled' status, got: {final_status}"
        )
        print(f"  Job successfully canceled. Final status: {final_status}")
