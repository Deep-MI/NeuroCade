"""Regression tests for delivery at budget/duplicate stops and Bash validation."""
import asyncio
import subprocess

import pytest
from api_service.assistant.loop import AssistantLoop
from api_service.assistant.tool_execution_store import AssistantToolExecutionStore
from api_service.assistant.tool_executor import AssistantToolExecutor
from api_service.assistant.tools.definition import ToolDefinition
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow
from langchain_core.messages import AIMessage

from backend_common.providers import ModelConfig, provider_registry


def workflow(script):
    return dict(id="mean", image="vnmd/fsl_6.0.7.23:20261013", description="mean", details="mean",
                inputs=[dict(name="input", description="input")], script=script)


@pytest.mark.parametrize("script", [
    'SRC="${INPUTS[0]}/sub"\necho "prefix ${SRC}"',
    'echo "ERROR: missing ${INPUTS[0]}"',
    'echo "$(dirname "${INPUTS[0]}")"',
    '# an unmatched " in a comment\necho "${INPUTS[0]}/sub"',
    'for ITEM in one two; do echo "${ITEM}"; done',
])
def test_valid_shell_paths_and_locals(script):
    NeuroimagingWorkflow.model_validate(workflow(script))


@pytest.mark.parametrize("script", [
    'echo ${INPUTS[0]}/sub',
    'echo "prefix" ${INPUTS[0]}',
    'echo "$(dirname ${INPUTS[0]})"',
])
def test_unquoted_paths_still_rejected(script):
    with pytest.raises(ValueError, match="double-quote"):
        NeuroimagingWorkflow.model_validate(workflow(script))


@pytest.mark.parametrize("name", ["FREESURFER_HOME", "FSLDIR", "ANTSPATH", "RUN_ID"])
def test_image_variables_are_checked_at_execution(name):
    script = 'set -u\nprintf "%s" "${' + name + '}"'
    NeuroimagingWorkflow.model_validate(workflow(script))
    present = subprocess.run(['/bin/bash', '-c', script], env={name: '/image/tool'}, capture_output=True, text=True)
    assert present.returncode == 0
    assert present.stdout == '/image/tool'
    missing = subprocess.run(['/bin/bash', '-c', script], env={}, capture_output=True, text=True)
    assert missing.returncode != 0
    assert name in missing.stderr


def state():
    return dict(provider_config=ModelConfig("test", "openai_compatible", "model"),
                system_prompt="system", conversation=[], round_count=18, max_rounds=18,
                tool_calls_log=[dict(name="tool_workflow_test", arguments=dict(definition=workflow('echo "${INPUTS[0]}"')),
                                     details=dict(status="schema_valid"))])


@pytest.mark.parametrize("response", [AIMessage(content="Here is what was verified."), RuntimeError("provider down"),
                                     AIMessage(content="", tool_calls=[dict(id="call", name="read", args={})])])
def test_budget_finalization_has_no_tools_and_keeps_draft(monkeypatch, tmp_path, response):
    class Model:
        def bind_tools(self, tools):
            pytest.fail("finalization must not bind tools")
        async def ainvoke(self, messages):
            assert "Tool use is now disabled" in messages[-1].content
            if isinstance(response, Exception):
                raise response
            return response
    monkeypatch.setattr(provider_registry, "build_chat_model", lambda **kwargs: Model())
    loop = AssistantLoop(None, config_dir=tmp_path)
    result = asyncio.run(loop._model_turn(state()))
    assert result["status"] == "completed"
    assert result["pending_tool_calls"] == []
    assert "```yaml" in result["final_response"]
    assert "id: mean" in result["final_response"]


def test_duplicate_call_requests_finalization_without_execution():
    async def execute(*args):
        pytest.fail("identical call must not execute")
    tool = ToolDefinition("tool_workflow_test", "", {}, execute)
    executor = AssistantToolExecutor(AssistantToolExecutionStore())
    current = state()
    current.update(round_count=16, tool_definitions=[tool], pending_tool_calls=[dict(call_id="again", name=tool.name, arguments={})],
                   last_tool_call_fingerprint=executor.fingerprint(tool.name, {}))
    result = asyncio.run(executor.execute(current))
    assert result["pending_tool_calls"] == []
    assert result["finalization_reason"]
    assert "final_response" not in result
    assert result["conversation"][-1]["call_id"] == "again"
    assert result["tool_calls_log"] == current["tool_calls_log"]


def test_duplicate_stop_finalizes_before_round_limit(monkeypatch, tmp_path):
    class Model:
        async def ainvoke(self, messages):
            return AIMessage(content="The validation error remains unresolved.")
    monkeypatch.setattr(provider_registry, "build_chat_model", lambda **kwargs: Model())
    current = state()
    current.update(round_count=3, finalization_reason="Repeated identical validation call.")
    result = asyncio.run(AssistantLoop(None, config_dir=tmp_path)._model_turn(current))
    assert result["status"] == "completed"
    assert "validation error" in result["final_response"]
    assert "```yaml" in result["final_response"]


def test_fallback_preserves_latest_draft_even_when_validation_fails(monkeypatch, tmp_path):
    def fail(**kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(provider_registry, "build_chat_model", fail)
    current = state()
    current["tool_calls_log"].append(dict(name="tool_workflow_test", arguments=dict(definition={"id": "broken"}), is_error=True))
    result = asyncio.run(AssistantLoop(None, config_dir=tmp_path)._model_turn(current))
    assert "id: mean" not in result["final_response"]
    assert "id: broken" in result["final_response"]
    assert "Latest workflow test: validation_failed" in result["final_response"]


def test_no_draft_failure_returns_honest_summary(monkeypatch, tmp_path):
    def fail(**kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(provider_registry, "build_chat_model", fail)
    current = state()
    current["tool_calls_log"] = []
    result = asyncio.run(AssistantLoop(None, config_dir=tmp_path)._model_turn(current))
    assert "No workflow draft is available" in result["final_response"]


def test_fallback_preserves_latest_execution_failure(monkeypatch, tmp_path):
    def fail(**kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(provider_registry, "build_chat_model", fail)
    current = state()
    current["tool_calls_log"].append(dict(name="tool_workflow_test",
        arguments=dict(definition=workflow('echo "newest correction"')),
        details=dict(status="failed"), is_error=True, result="Required output missing"))
    result = asyncio.run(AssistantLoop(None, config_dir=tmp_path)._model_turn(current))
    assert 'newest correction' in result['final_response']
    assert 'Latest workflow test: failed' in result['final_response']
    assert 'Required output missing' in result['final_response']
    assert 'Latest validated draft' not in result['final_response']


@pytest.mark.parametrize("round_count", [18, 25, 100])
def test_unlimited_rounds_continue_tool_calls(monkeypatch, tmp_path, round_count):
    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="", tool_calls=[dict(id="next", name="read", args={})])

    monkeypatch.setattr(provider_registry, "build_chat_model", lambda **kwargs: Model())
    current = state()
    current.update(max_rounds=0, round_count=round_count, tool_specs=[])
    result = asyncio.run(AssistantLoop(None, config_dir=tmp_path)._model_turn(current))
    assert result["status"] == "running"
    assert result["round_count"] == round_count + 1
    assert result["pending_tool_calls"][0]["name"] == "read"


def test_unlimited_rounds_still_honor_explicit_finalization(monkeypatch, tmp_path):
    class Model:
        async def ainvoke(self, messages):
            return AIMessage(content="Stopped after repeated identical calls.")

    monkeypatch.setattr(provider_registry, "build_chat_model", lambda **kwargs: Model())
    current = state()
    current.update(max_rounds=0, finalization_reason="Repeated identical call.")
    result = asyncio.run(AssistantLoop(None, config_dir=tmp_path)._model_turn(current))
    assert result["status"] == "completed"
    assert result["pending_tool_calls"] == []
