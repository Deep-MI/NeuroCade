"""Typed structured-response contract shared by prompting and parsing."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator


class AssistantToolCall(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class FinalResponse(BaseModel):
    kind: Literal["final"]
    reasoning: str | None = None
    content: str = Field(min_length=1)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def content_is_not_blank(self) -> FinalResponse:
        if not self.content.strip():
            raise ValueError("Final response content must not be blank")
        return self


class ToolCallsResponse(BaseModel):
    kind: Literal["tool_calls"]
    reasoning: str | None = None
    message: str | None = None
    tool_calls: list[AssistantToolCall] = Field(min_length=1)

    model_config = {"extra": "forbid"}


AssistantStructuredResponse = Annotated[FinalResponse | ToolCallsResponse, Field(discriminator="kind")]
RESPONSE_ADAPTER = TypeAdapter(AssistantStructuredResponse)
RESPONSE_INSTRUCTION = (
    "Return only JSON matching one of these forms: "
    '{"kind":"final","reasoning":"optional","content":"answer"} or '
    '{"kind":"tool_calls","reasoning":"optional","message":"optional progress update",'
    '"tool_calls":[{"name":"tool_name","arguments":{}}]}. '
    "Do not wrap the JSON in markdown."
)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def _parse(raw_text: str) -> AssistantStructuredResponse | None:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip().removesuffix("```").strip()
    try:
        return RESPONSE_ADAPTER.validate_python(json.loads(text))
    except (json.JSONDecodeError, ValidationError):
        return None


async def coerce_structured_response(
    model: Any,
    messages: list[BaseMessage],
    raw_text: str,
) -> AssistantStructuredResponse:
    """Parse the response or ask the same model to repair it once."""
    parsed = _parse(raw_text)
    if parsed is not None:
        return parsed

    repaired = await model.ainvoke(
        [
            *messages,
            AIMessage(content=raw_text),
            HumanMessage(content=f"Your previous reply violated the required contract. {RESPONSE_INSTRUCTION}"),
        ]
    )
    parsed = _parse(_message_text(repaired))
    if parsed is not None:
        return parsed
    raise HTTPException(status_code=502, detail="Assistant model did not return a usable JSON response")
