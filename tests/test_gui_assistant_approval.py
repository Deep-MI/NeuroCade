"""Browser regression coverage for assistant mutation confirmation."""

import json

pytest_plugins = ["conftest_gui"]


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
    payload = {
        "message": {
            "role": "assistant",
            "content": f"Please confirm that I may {approval['description']}.",
        },
        "approval_request": approval,
    }
    body = f"event: done\ndata: {json.dumps(payload)}\n\n"
    page.route(
        "**/api/app/assistant/turns",
        lambda route: route.fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=body,
        ),
    )

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
