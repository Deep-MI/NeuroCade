"""Native provider response adapter used by the assistant model driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


def response_text(response: Any) -> str:
    raw_content = getattr(response, "content", "")
    if isinstance(raw_content, list):
        return "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in raw_content
        )
    return str(raw_content or "")


@dataclass(frozen=True)
class ModelProtocolResult:
    final_content: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    assistant_message: str | None = None
    reasoning: str | None = None
    usage: dict[str, Any] | None = None
    raw_text: str = ""


class NativeToolProtocol:
    """Decode LangChain's normalized native tool-call response shape."""

    @staticmethod
    def parse(response: Any) -> ModelProtocolResult:
        text = response_text(response)
        calls = [
            {
                "call_id": str(call.get("id") or uuid4()),
                "name": str(call.get("name") or ""),
                "arguments": dict(call.get("args") or {}),
            }
            for call in list(getattr(response, "tool_calls", []) or [])
        ]
        additional = getattr(response, "additional_kwargs", {}) or {}
        reasoning = additional.get("reasoning_content") or additional.get("reasoning")
        usage = getattr(response, "usage_metadata", None)
        return ModelProtocolResult(
            final_content=None if calls else text.strip() or None,
            calls=calls,
            assistant_message=text.strip()[:4000] or None,
            reasoning=str(reasoning) if reasoning else None,
            usage=dict(usage) if isinstance(usage, dict) else None,
            raw_text=text,
        )
