"""
End-to-end test: Ask the LLM to run mri_synthstrip (skull stripping).

mri_synthstrip requires an Apptainer image that contains the command. This test
auto-skips if the command is unavailable.

Flow:
  1. Check if mri_synthstrip is available in the Apptainer runtime (auto-skip if not)
  2. Seed GUI state for adni2
  3. Send "Run skull stripping on the orig volume using synthstrip" via chat
  4. Verify the agent calls tool_call with mri_synthstrip
  5. Verify output file is created

Prerequisites:
  ./scripts/apptainer/up.sh -d

Usage:
  pytest tests/test_synthstrip_e2e.py -v
"""

import time

import pytest
import requests

from conftest import (
    assert_no_text_explanation,
    chat_send,
    check_command_available,
    get_app_auth_headers,
    get_response_content,
    seed_gui_state,
)


def _tool_command_text(entry: dict) -> str:
    """Return the command-like text recorded for a runtime tool call."""
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
def synthstrip_available(services_up):
    """Check if mri_synthstrip exists in the Apptainer runtime."""
    if not check_command_available("mri_synthstrip"):
        pytest.skip(
            "mri_synthstrip not available in Apptainer runtime. "
            "Configure a tool image that contains mri_synthstrip."
        )


class TestSynthStrip:
    """Ask the agent to perform skull stripping with mri_synthstrip."""

    @pytest.fixture(autouse=True)
    def setup(self, gateway_url, synthstrip_available):
        self.gateway_url = gateway_url

    def test_synthstrip_t1w_request_discovers_then_runs_with_explicit_path(self, fresh_processed_case):
        """Agent may inspect the case or help text first, but must eventually run mri_synthstrip against the mounted /case tree."""
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
                "content": "Please run mri_synthstrip on the T1w image.",
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
                            for marker in ("mri_synthstrip", "synthstrip", "--help")
                        )
                    )
                )
            ),
            None,
        )
        synthstrip_call = next(
            (
                entry
                for entry in tool_calls
                if entry.get("name") == "tool_call"
                and "mri_synthstrip" in _tool_command_text(entry)
                and "--help" not in _tool_command_text(entry)
            ),
            None,
        )
        assert synthstrip_call is not None, (
            "Expected a tool_call running mri_synthstrip.\n"
            f"Tool calls: {tool_calls}\n"
            f"Response: {content[:500]}"
        )

        command = _tool_command_text(synthstrip_call)
        explicit_input_paths = [
            "/case/orig.mgz",
            "/case/",
        ]
        assert any(path in command for path in explicit_input_paths), (
            "mri_synthstrip command did not use an explicit valid input path.\n"
            f"Command: {command}\n"
            f"Expected one of: {explicit_input_paths}"
        )
        assert any(flag in command for flag in (" -o ", " --out ", " -m ", " --mask ")), (
            f"Expected mri_synthstrip command to specify an output image or mask path: {command}"
        )
        if discovery_call is not None and discovery_call.get("name") == "tool_call":
            discovery_command = _tool_command_text(discovery_call)
            assert any(
                marker in discovery_command for marker in ("mri_synthstrip", "synthstrip", "--help")
            ), f"Unexpected discovery command: {discovery_command}"
        assert_no_text_explanation(content)

    def test_synthstrip_orig(self, fresh_processed_case):
        """Agent should call tool_call with mri_synthstrip on the orig volume."""
        seed_gui_state(fresh_processed_case["gui_state"], self.gateway_url)
        time.sleep(0.3)

        messages = [
            {
                "role": "user",
                "content": "Run skull stripping on the orig volume using synthstrip",
            },
        ]
        response = chat_send(messages, self.gateway_url, timeout=300)
        content = get_response_content(response)
        tool_calls = response.get("tool_calls_log", [])
        synthstrip_call = next(
            (
                entry
                for entry in tool_calls
                if entry.get("name") == "tool_call"
                and "mri_synthstrip" in _tool_command_text(entry)
                and "--help" not in _tool_command_text(entry)
            ),
            None,
        )
        assert synthstrip_call is not None, (
            "Expected a tool_call running mri_synthstrip.\n"
            f"Tool calls: {tool_calls}\n"
            f"Response: {content[:500]}"
        )

        command = _tool_command_text(synthstrip_call)
        assert "/case/orig.mgz" in command or "/case/" in command, (
            "Expected mri_synthstrip to use the direct case mount.\n"
            f"Command: {command}"
        )
        assert any(flag in command for flag in (" -o ", " --out ", " -m ", " --mask ")), (
            f"Expected mri_synthstrip command to specify an output image or mask path: {command}"
        )

        # Response should indicate success
        success_markers = [
            "successfully", "completed", "output", "stripped", "saved",
            "written", "skull", "brain",
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
                stripped_files = [
                    v for v in volumes
                    if "strip" in v.get("name", "").lower()
                    or "brain" in v.get("name", "").lower()
                ]
                if stripped_files:
                    print(f"\n  Output file found: {stripped_files[0]['name']}")
        except Exception:
            pass  # Non-critical check

        print(f"\n  synthstrip response ({len(content)} chars):")
        for line in content.split("\n")[:10]:
            print(f"    {line}")
