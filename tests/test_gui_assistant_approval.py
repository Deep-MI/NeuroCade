"""Browser regression coverage for assistant mutation confirmation."""

import json

pytest_plugins = ["conftest_gui"]


def _mock_approval_response(page, approval):
    payload = {
        "message": {
            "role": "assistant",
            "content": f"Please confirm that I may {approval['description']}.",
        },
        "approval_request": approval,
    }
    page.route(
        "**/api/app/assistant/turns",
        lambda route: route.fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=f"event: done\ndata: {json.dumps(payload)}\n\n",
        ),
    )


def test_assistant_mutation_confirmation_can_be_declined(page):
    """Render the exact pending action and keep it inert until approval."""
    approval = {
        "name": "tool_call",
        "arguments": {"tool_id": "fastsurfer_segmentation", "inputs": ["/case/mri/001.mgz"]},
        "digest": "a" * 64,
        "description": "run FastSurfer — Segmentation",
        "presentation": {
            "kind": "workflow",
            "title": "FastSurfer — Segmentation",
            "description": "Run FastSurfer segmentation without cortical surface reconstruction.",
            "details": "Generate a segmentation and statistics in the active case.",
            "inputs": [{
                "name": "t1",
                "description": "T1-weighted input volume.",
                "path": "/case/mri/001.mgz",
            }],
            "outputs": [{
                "name": "whole_brain_segmentation",
                "description": "FastSurfer whole-brain segmentation.",
                "path": "mri/aparc.DKTatlas+aseg.deep.mgz",
            }],
            "execution": {"mode": "background", "gpu": True},
        },
    }
    _mock_approval_response(page, approval)

    page.locator("input.chat-input").fill("Create a note")
    page.get_by_role("button", name="Send").click()

    confirmation = page.get_by_role("group", name="Confirm assistant action")
    confirmation.wait_for(state="visible")
    confirmation_text = confirmation.inner_text()
    assert "Run FastSurfer — Segmentation?" in confirmation_text
    assert "T1-weighted input volume." in confirmation_text
    assert "/case/mri/001.mgz" in confirmation_text
    assert "Background workflow · GPU enabled" in confirmation_text
    assert confirmation.get_by_role("button", name="Start workflow").is_visible()
    confirmation.get_by_role("button", name="Decline").click()
    confirmation.wait_for(state="detached")


def test_config_edit_confirmation_uses_structured_action_layout(page):
    approval = {
        "name": "tool_config_upsert",
        "arguments": {"definition": {"id": "my_segmentation"}},
        "digest": "b" * 64,
        "description": "save my segmentation",
        "presentation": {
            "kind": "action",
            "action": "config_upsert",
            "title": "Save My segmentation?",
            "description": "Run a private segmentation workflow.",
            "confirm_label": "Save workflow",
            "tone": "warning",
            "sections": [
                {
                    "label": "Catalog change",
                    "rows": [
                        {"label": "Operation", "value": "Create private workflow", "code": False},
                        {"label": "Workflow ID", "value": "my_segmentation", "code": True},
                        {"label": "Image", "value": "vnmd/example:1.0", "code": True},
                    ],
                },
                {
                    "label": "Execution",
                    "rows": [
                        {"label": "Scheduling", "value": "Background · GPU enabled", "code": False},
                    ],
                },
            ],
            "details": [
                {"summary": "Workflow script", "content": "tool --input \"${INPUTS[0]}\"", "language": "bash"},
            ],
        },
    }
    _mock_approval_response(page, approval)

    page.locator("input.chat-input").fill("Create a workflow")
    page.get_by_role("button", name="Send").click()

    confirmation = page.get_by_role("group", name="Confirm assistant action")
    confirmation.wait_for(state="visible")
    confirmation_text = confirmation.inner_text()
    assert "Save My segmentation?" in confirmation_text
    assert "Create private workflow" in confirmation_text
    assert "my_segmentation" in confirmation_text
    assert "Background · GPU enabled" in confirmation_text
    assert confirmation.get_by_role("button", name="Save workflow").is_visible()
    confirmation.get_by_text("Workflow script").click()
    assert 'tool --input "${INPUTS[0]}"' in confirmation.inner_text()
    confirmation.get_by_role("button", name="Decline").click()
    confirmation.wait_for(state="detached")
