"""Token budgeting and deterministic domain summaries for assistant history."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

DOMAIN_SUMMARY_HEADER = "[Compacted NeuroCade context]"


def estimate_text_tokens(text: str) -> int:
    """Return a conservative tokenizer-independent estimate."""
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate tokens for one serializable conversation message."""
    return estimate_text_tokens(json.dumps(message, ensure_ascii=False, default=str)) + 3


def estimate_messages_tokens(messages: Iterable[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def compact_text_to_tokens(text: str, max_tokens: int) -> str:
    """Keep both ends of text under an approximate token limit."""
    character_limit = max(max_tokens * 4, 1)
    if len(text) <= character_limit:
        return text
    marker = "\n[... compacted ...]\n"
    available = max(character_limit - len(marker), 2)
    head = available // 2
    return text[:head] + marker + text[-(available - head):]


def _message_excerpt(message: dict[str, Any], max_tokens: int = 240) -> str:
    content = message.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    return " ".join(compact_text_to_tokens(content, max_tokens).split())


def build_domain_summary(
    messages: list[dict[str, Any]],
    *,
    previous_summary: str | None = None,
    max_tokens: int = 4_000,
) -> str:
    """Build a bounded, evidence-preserving NeuroCade context checkpoint.

    This is intentionally deterministic: compaction must remain available when
    the configured model is offline or already at its context limit.
    """
    user_goals: list[str] = []
    outcomes: list[str] = []
    tool_evidence: list[str] = []
    context: list[str] = []
    for message in messages:
        excerpt = _message_excerpt(message)
        if not excerpt:
            continue
        role = message.get("role")
        if role == "user":
            user_goals.append(excerpt)
        elif role == "assistant":
            outcomes.append(excerpt)
        elif role == "tool":
            name = str(message.get("name") or "tool")
            tool_evidence.append(f"{name}: {excerpt}")
        else:
            context.append(excerpt)

    sections = [DOMAIN_SUMMARY_HEADER]
    if previous_summary:
        sections.extend(("Prior compacted context:", compact_text_to_tokens(previous_summary, 1_000)))
    for title, values in (
        ("User goals and requests:", user_goals),
        ("Assistant outcomes and decisions:", outcomes),
        ("Tool and workflow evidence:", tool_evidence),
        ("Workspace/case context:", context),
    ):
        if values:
            sections.append(title)
            sections.extend(f"- {value}" for value in values[-20:])
    sections.append(
        "Treat this summary as prior context, not fresh tool evidence. Re-inspect files or run status tools before claims that require current state."
    )
    return compact_text_to_tokens("\n".join(sections), max_tokens)


def select_recent_messages(
    messages: list[dict[str, Any]],
    *,
    token_budget: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split history at user-turn boundaries into compacted and recent spans."""
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "user" or not groups:
            groups.append([])
        groups[-1].append(message)

    recent_groups: list[list[dict[str, Any]]] = []
    used = 0
    for group in reversed(groups):
        size = estimate_messages_tokens(group)
        if recent_groups and used + size > token_budget:
            break
        recent_groups.append(group)
        used += size
        if used >= token_budget:
            break
    recent_groups.reverse()
    recent_count = sum(len(group) for group in recent_groups)
    return messages[:-recent_count] if recent_count else messages, [item for group in recent_groups for item in group]
