"""
End-to-end test: Ask the LLM to show volume header info via mri_info.

Flow:
  1. Seed GUI state for a processed demo case (idle, volumes loaded)
  2. Send "Show me the header info for the orig volume" via chat API
  3. Verify the agent calls tool_call with mri_info (from runtime logs)
  4. Verify the response contains volume metadata (dimensions, voxel sizes)
  5. Verify the response is NOT a text explanation

Prerequisites:
  ./scripts/compose/up.sh -d
  a processed demo case must exist under the configured test outputs directory

Usage:
  pytest tests/test_mri_info_e2e.py -v
"""

import time

import pytest

from conftest import (
    assert_no_text_explanation,
    assert_tool_executed,
    chat_send,
    runtime_logs_since,
    get_response_content,
    seed_gui_state,
    utc_timestamp,
)


@pytest.fixture(autouse=True)
def require_services(services_up):
    """Skip all tests in this module if local NeuroCade stack is not running."""
    pass


class TestMriInfo:
    """Ask the agent to run mri_info on the orig volume."""

    @pytest.fixture(autouse=True)
    def setup(self, gateway_url):
        """Store the gateway URL for chat and GUI state requests."""
        self.gateway_url = gateway_url

    def test_mri_info_orig(self, adni2_state):
        """Agent should call tool_call with mri_info and return volume metadata."""
        # Seed GUI state
        seed_gui_state(adni2_state, self.gateway_url)

        log_ts = utc_timestamp()
        time.sleep(0.3)

        messages = [
            {"role": "user", "content": "Show me the header info for the orig volume"},
        ]
        response = chat_send(messages, self.gateway_url, timeout=180)
        content = get_response_content(response)

        # Verify tool execution from logs
        logs = runtime_logs_since(since=log_ts)
        assert_tool_executed(logs)

        # The agent should have used catalog routing with mri_info.
        assert "mri_info" in logs, (
            "Expected 'mri_info' in proxy logs but not found"
        )

        # Response should contain volume metadata markers
        metadata_markers = [
            "dimension", "voxel", "type", "resolution", "tr", "te",
            "intensity", "orient", "volume", "256", "1.0",
        ]
        content_lower = content.lower()
        found = [m for m in metadata_markers if m in content_lower]
        assert len(found) >= 2, (
            f"Expected volume metadata in response, found markers: {found}\n"
            f"Content preview: {content[:500]}"
        )

        # Should NOT be a text explanation
        assert_no_text_explanation(content)

        print(f"\n  mri_info response ({len(content)} chars):")
        for line in content.split("\n")[:15]:
            print(f"    {line}")

    def test_mri_info_specific_file(self, adni2_state):
        """Agent should run mri_info on a specific file when asked."""
        seed_gui_state(adni2_state, self.gateway_url)

        log_ts = utc_timestamp()
        time.sleep(0.3)

        messages = [
            {
                "role": "user",
                "content": "Run mri_info on the brainmask (mask.mgz) and tell me the voxel size",
            },
        ]
        response = chat_send(messages, self.gateway_url, timeout=180)
        content = get_response_content(response)

        logs = runtime_logs_since(since=log_ts)
        assert_tool_executed(logs)
        assert "mri_info" in logs

        # Response should mention voxel/voxsize
        content_lower = content.lower()
        assert any(
            m in content_lower for m in ["voxel", "vox", "mm", "resolution", "size"]
        ), f"Response doesn't mention voxel info: {content[:300]}"

        assert_no_text_explanation(content)
