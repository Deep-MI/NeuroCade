"""Parse and repair structured assistant model responses."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError


class AssistantToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantStructuredResponse(BaseModel):
    kind: Literal["final", "tool_calls"]
    reasoning: str | None = None
    message: str | None = None
    content: str | None = None
    tool_calls: list[AssistantToolCall] = Field(default_factory=list)


def clean_json_payload(raw_text: str) -> str:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    return stripped


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _parse_structured_json(raw_text: str) -> AssistantStructuredResponse | None:
    try:
        payload = json.loads(clean_json_payload(raw_text))
    except json.JSONDecodeError:
        return None
    try:
        return AssistantStructuredResponse.model_validate(payload)
    except ValidationError:
        return None


def _is_usable_response(response: AssistantStructuredResponse) -> bool:
    if response.kind == "final":
        return bool((response.content or "").strip())
    return bool(response.tool_calls)


async def coerce_structured_response(
    model: Any,
    messages: list[BaseMessage],
    raw_text: str,
) -> AssistantStructuredResponse:
    """Return a usable structured response, asking the model to repair malformed JSON once."""
    parsed = _parse_structured_json(raw_text)
    if parsed is not None and _is_usable_response(parsed):
        return parsed

    repair_messages = list(messages) + [
        AIMessage(content=raw_text),
        HumanMessage(
            content=(
                "Your previous reply was not valid usable JSON for the required schema. "
                "Return only valid JSON using one of these shapes: "
                '{"kind":"final","reasoning":"short optional string","content":"answer"} '
                'or {"kind":"tool_calls","reasoning":"short optional string","message":"optional user-facing progress update","tool_calls":[{"name":"tool_name","arguments":{}}]}. '
                "A final response must include non-empty content. A tool_calls response must include at least one tool call. "
                "If a tool failed and you cannot continue, return a final response explaining the failure."
            )
        ),
    ]
    repair_response = await model.ainvoke(repair_messages)
    repaired = _parse_structured_json(_message_text(repair_response))
    if repaired is not None and _is_usable_response(repaired):
        return repaired

    raise HTTPException(status_code=502, detail="Assistant model did not return a usable JSON response")
