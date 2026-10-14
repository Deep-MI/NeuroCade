"""Evidence returned to the agent during workflow authoring."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from api_service.assistant.tools import probe_tools
from api_service.assistant.tools.catalog_tools import AssistantCatalogTools
from api_service.assistant.tools.definition import ToolExecutionContext


def definition():
    return yaml.safe_load((Path(__file__).parent / "fixtures/workflows/freesurfer_segmentbs.yaml").read_text())


def test_draft_review_exposes_alias_and_required_output_gap():
    draft = definition()
    for output in draft["outputs"]:
        output["required"] = False
    # No executor: review must not save, submit, or need a runtime container.
    tools = AssistantCatalogTools(cast(Any, None))
    result = tools.config_validate({}, ToolExecutionContext("review"), {"definition": draft})
    assert not result.is_error
    assert result.details["saved"] is False
    assert result.details["executed"] is False
    assert result.details["status"] == "schema_valid"
    assert {w["code"] for w in result.details["warnings"]} == {"no_required_output", "directory_output_alias"}
    assert result.details["outputs"][0]["case_relative_path"] == "brainstem/mri/brainstemSsLabels.v13.mgz"
    assert result.details["output_mount"]["same_directory_as"] == "${CASE_ROOT}/brainstem"


def test_draft_review_rejects_broken_bash_without_saving():
    draft = definition()
    draft["script"] = "if then"
    result = AssistantCatalogTools(cast(Any, None)).config_validate({}, ToolExecutionContext("review"), {"definition": draft})
    assert result.is_error
    assert "invalid Bash" in result.content


@pytest.mark.parametrize("returncode", [0, 1])
def test_probe_reports_command_failure_and_evidence_limits(monkeypatch, returncode):
    async def execute(request, **kwargs):
        assert request.container_run.isolated
        return SimpleNamespace(returncode=returncode, stdout="diagnostic evidence", stderr="assertion failed" if returncode else "")
    monkeypatch.setattr(probe_tools, "execute_runtime_request_async", execute)
    monkeypatch.setattr(probe_tools, "runtime_image_spec", lambda value: value)
    result = asyncio.run(probe_tools.AssistantProbeTools(settings=None).probe(
        {}, ToolExecutionContext("probe"), {"image": "vnmd/test:1", "script": "test -f /tmp/result"},
    ))
    assert result.is_error is bool(returncode)
    assert result.details["stdout"] == "diagnostic evidence"
    assert "real bind-mount aliases" in result.details["not_verified"]
    assert result.details["status"] == ("command_failed" if returncode else "completed")
