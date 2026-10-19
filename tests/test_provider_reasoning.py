"""Ensure provider reasoning reaches the existing assistant display contract."""

import pytest
from api_service.assistant.model_protocols import NativeToolProtocol
from langchain_core.messages import AIMessageChunk

from backend_common.reasoning_model import ReasoningChatOpenAI


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
def test_streaming_reasoning_survives_aggregation_and_protocol(field):
    model = ReasoningChatOpenAI(model="test", api_key="test")
    chunks = []
    for delta in [
        {"role": "assistant", field: "Check "},
        {field: "the inputs."},
        {"content": "Ready", "tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "case_file_tree", "arguments": "{}"}}]},
    ]:
        chunk = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": delta}]}, AIMessageChunk, None,
        )
        assert chunk is not None
        chunks.append(chunk.message)
    parsed = NativeToolProtocol.parse(chunks[0] + chunks[1] + chunks[2])
    assert parsed.reasoning == "Check the inputs."
    assert parsed.assistant_message == "Ready"
    assert parsed.calls[0]["name"] == "case_file_tree"


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
def test_nonstreaming_reasoning_preserved(field):
    model = ReasoningChatOpenAI(model="test", api_key="test")
    result = model._create_chat_result({"choices": [{"message": {"role": "assistant", "content": "Ready", field: "Check inputs."}, "finish_reason": "stop"}]})
    parsed = NativeToolProtocol.parse(result.generations[0].message)
    assert parsed.reasoning == "Check inputs."
    assert parsed.final_content == "Ready"


def test_plain_and_usage_only_chunks_do_not_invent_reasoning():
    model = ReasoningChatOpenAI(model="test", api_key="test")
    for payload in [{"choices": [{"delta": {"content": "Ready"}}]}, {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}]:
        chunk = model._convert_chunk_to_generation_chunk(payload, AIMessageChunk, None)
        assert chunk is not None
        assert NativeToolProtocol.parse(chunk.message).reasoning is None
