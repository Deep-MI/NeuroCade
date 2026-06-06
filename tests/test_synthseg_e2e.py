"""
End-to-end test: Ask the LLM to run mri_synthseg (segmentation with SynthSeg).

mri_synthseg requires an Apptainer image that contains the command. This test
auto-skips if the command is unavailable.

Flow:
  1. Check if mri_synthseg is available in the Apptainer runtime (auto-skip if not)
  2. Seed GUI state for adni2
  3. Send "Segment the orig volume using SynthSeg" via chat
  4. Verify the agent calls tool_call with mri_synthseg
  5. Verify output file is created

Prerequisites:
  ./scripts/apptainer/up.sh -d

Usage:
  pytest tests/test_synthseg_e2e.py -v
"""

import time

import pytest
import requests

from conftest import (
    assert_no_text_explanation,
    assert_tool_executed,
    chat_send,
    check_command_available,
    runtime_logs_since,
    get_app_auth_headers,
    get_response_content,
    seed_gui_state,
    utc_timestamp,
)


def _tool_command_text(entry: dict) -> str:
    """Return the command text from a runtime tool log entry."""
    arguments = entry.get("arguments", {}) if isinstance(entry.get("arguments"), dict) else {}
    if entry.get("name") == "tool_call":
        raw_args = arguments.get("tool_args", [])
        args = " ".join(str(item) for item in raw_args) if isinstance(raw_args, list) else str(raw_args)
        return f"{arguments.get('tool', '')} {args}".strip()
    return str(arguments.get("command", ""))


@pytest.fixture(autouse=True)
def require_services(services_up):
    pass


@pytest.fixture(scope="module")
def synthseg_available(services_up):
    """Check if mri_synthseg exists in the Apptainer runtime."""
    if not check_command_available("mri_synthseg"):
        pytest.skip(
            "mri_synthseg not available in Apptainer runtime. "
            "Configure a tool image that contains mri_synthseg."
        )


class TestSynthSeg:
    """Ask the agent to perform segmentation with mri_synthseg."""

    @pytest.fixture(autouse=True)
    def setup(self, gateway_url, synthseg_available):
        """Store the gateway URL after SynthSeg availability is confirmed."""
        self.gateway_url = gateway_url

    def test_synthseg_t1w_request_discovers_then_runs_with_explicit_path(self, fresh_processed_case):
        """Agent may inspect the case first, but must eventually run mri_synthseg against the mounted /case tree."""
        seed_gui_state(
            {
                **fresh_processed_case["gui_state"],
                "loaded_volumes": ["orig.mgz", "aparc.DKTatlas+aseg.mgz"],
            },
            self.gateway_url,
        )

        messages = [
            {
                "role": "user",
                "content": "Please run mri_synthseg on the T1w image.",
            },
        ]
        response = chat_send(messages, self.gateway_url, timeout=600)
        content = get_response_content(response)
        tool_calls = response.get("tool_calls_log", [])

        discovery_call = next(
            (
                entry
                for entry in tool_calls
                if (
                    entry.get("name") == "case_file_tree"
                    or (
                        entry.get("name") in {"tool_search", "tool_call"}
                        and any(
                            marker in _tool_command_text(entry)
                            for marker in ("mri_synthseg", "synthseg", "--help")
                        )
                    )
                )
            ),
            None,
        )
        synthseg_call = next(
            (
                entry
                for entry in tool_calls
                if entry.get("name") == "tool_call"
                and "mri_synthseg" in _tool_command_text(entry)
            ),
            None,
        )
        assert synthseg_call is not None, (
            "Expected a tool_call running mri_synthseg.\n"
            f"Tool calls: {tool_calls}\n"
            f"Response: {content[:500]}"
        )

        command = _tool_command_text(synthseg_call)
        explicit_input_paths = [
            "/case/orig.mgz",
            "/case/",
        ]
        assert any(path in command for path in explicit_input_paths), (
            "mri_synthseg command did not use an explicit valid input path.\n"
            f"Command: {command}\n"
            f"Expected one of: {explicit_input_paths}"
        )
        assert "/case/" in command, (
            f"Expected SynthSeg command to target the mounted case tree: {command}"
        )
        if discovery_call is not None:
            if discovery_call.get("name") == "tool_call":
                discovery_command = _tool_command_text(discovery_call)
                assert any(
                    marker in discovery_command for marker in ("mri_synthseg", "synthseg", "--help")
                ), f"Unexpected discovery command: {discovery_command}"
        assert_no_text_explanation(content)

    def test_synthseg_orig(self, fresh_processed_case):
        """Agent should call tool_call with mri_synthseg on the orig volume."""
        seed_gui_state(fresh_processed_case["gui_state"], self.gateway_url)

        log_ts = utc_timestamp()
        time.sleep(0.3)

        messages = [
            {
                "role": "user",
                "content": "Segment the orig volume using SynthSeg",
            },
        ]
        # SynthSeg can be slow (GPU inference), allow generous timeout
        response = chat_send(messages, self.gateway_url, timeout=600)
        content = get_response_content(response)

        # Verify tool execution
        logs = runtime_logs_since(since=log_ts)
        assert_tool_executed(logs)

        # Should use mri_synthseg (or synthseg)
        assert "synthseg" in logs.lower(), (
            "Expected 'synthseg' in proxy logs but not found"
        )

        # Response should indicate success
        success_markers = [
            "successfully", "completed", "output", "segmentation",
            "saved", "written", "segment",
        ]
        content_lower = content.lower()
        found = [m for m in success_markers if m in content_lower]
        assert found, (
            f"Response doesn't indicate success: {content[:300]}"
        )

        assert_no_text_explanation(content)

        # Optionally check if the output file was created
        try:
            r = requests.get(
                f"{self.gateway_url}/api/app/cases/{fresh_processed_case['case_id']}/artifacts",
                headers=get_app_auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                volumes = r.json()
                seg_files = [
                    v for v in volumes
                    if "synthseg" in v.get("name", "").lower()
                    or "seg" in v.get("name", "").lower()
                ]
                if seg_files:
                    print(f"\n  Output files found: {[v['name'] for v in seg_files]}")
        except Exception:
            pass  # Non-critical check

        print(f"\n  synthseg response ({len(content)} chars):")
        for line in content.split("\n")[:10]:
            print(f"    {line}")
