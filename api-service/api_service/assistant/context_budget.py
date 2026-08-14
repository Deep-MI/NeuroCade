"""Provider-neutral prompt accounting and pair-safe context bounding."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from api_service.assistant.compaction import estimate_text_tokens
from api_service.assistant.prompts import stringify_content

CONTEXT_OMISSION_NOTICE = (
    "[Context notice: earlier conversation messages were omitted and/or one "
    "included message was compacted to fit the configured prompt budget. "
    "The newest context was prioritized.]"
)


def message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    text = stringify_content(content)
    image_count = sum(
        1 for part in content if isinstance(part, dict) and part.get("type") == "image_url"
    ) if isinstance(content, list) else 0
    tool_calls = getattr(message, "tool_calls", None)
    tool_context = json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else ""
    return text + ("\n[image]" * image_count) + tool_context


def message_tokens(message: BaseMessage) -> int:
    return estimate_text_tokens(message_text(message)) + 3


def compact_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 80:
        return text[:limit]
    marker = f"\n\n[... omitted {len(text) - limit} characters ...]\n\n"
    available = max(limit - len(marker), 2)
    head = available // 2
    return text[:head] + marker + text[-(available - head):]


class ContextBudgeter:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def bound(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Keep instructions and newest complete native tool exchanges in budget."""
        character_limit = max(int(self.settings.assistant_prompt_max_characters), 1)
        token_limit = max(int(self.settings.assistant_prompt_max_tokens), 1)
        if not messages:
            return messages
        has_suffix = not isinstance(messages[-1], ToolMessage)
        suffix = [messages[-1]] if has_suffix else []
        middle = messages[1:-1] if has_suffix else messages[1:]
        fixed = [messages[0], *suffix]
        fixed_size = sum(len(message_text(message)) for message in fixed)
        fixed_tokens = sum(message_tokens(message) for message in fixed)
        total_size = fixed_size + sum(len(message_text(message)) for message in middle)
        total_tokens = fixed_tokens + sum(message_tokens(message) for message in middle)
        if total_size <= character_limit and total_tokens <= token_limit:
            return messages
        notice_tokens = estimate_text_tokens(CONTEXT_OMISSION_NOTICE) + 3
        if fixed_size + len(CONTEXT_OMISSION_NOTICE) > character_limit or fixed_tokens + notice_tokens > token_limit:
            raise ValueError(
                "Assistant prompt limits are too small for the complete "
                "system prompt, response contract, and context omission notice"
            )
        remaining = max(character_limit - fixed_size - len(CONTEXT_OMISSION_NOTICE), 0)
        remaining_tokens = max(token_limit - fixed_tokens - notice_tokens, 0)
        groups: list[list[BaseMessage]] = []
        for message in middle:
            if isinstance(message, ToolMessage) and groups and isinstance(groups[-1][0], AIMessage):
                groups[-1].append(message)
            else:
                groups.append([message])
        selected_groups: list[list[BaseMessage]] = []
        for group in reversed(groups):
            size = sum(len(message_text(message)) for message in group)
            tokens = sum(message_tokens(message) for message in group)
            if size > remaining or tokens > remaining_tokens:
                if not selected_groups and len(group) == 1 and remaining > 0:
                    message = group[0]
                    if isinstance(getattr(message, "content", None), str):
                        content_limit = min(remaining, remaining_tokens * 4)
                        selected_groups.append([
                            message.model_copy(update={"content": compact_text(str(message.content), content_limit)})
                        ])
                break
            selected_groups.append(group)
            remaining -= size
            remaining_tokens -= tokens
        selected_groups.reverse()
        selected = [message for group in selected_groups for message in group]
        return [messages[0], HumanMessage(content=CONTEXT_OMISSION_NOTICE), *selected, *suffix]
