"""Load configured runtime tools for assistant calls and analysis UI."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from backend_common.settings import ROOT_DIR


CONFIGURED_TOOLS_PATH = ROOT_DIR / "config" / "runtime_tools.json"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


class ConfiguredContainer(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    label: str
    image: str
    requires_license: bool = False


class ConfiguredToolUi(BaseModel):
    run_analysis: bool = False


class ConfiguredTool(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    label: str
    container_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    command: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    ui: ConfiguredToolUi = Field(default_factory=ConfiguredToolUi)


class RuntimeToolConfig(BaseModel):
    containers: list[ConfiguredContainer] = Field(default_factory=list)
    tools: list[ConfiguredTool] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "RuntimeToolConfig":
        container_ids = {container.id for container in self.containers}
        missing = sorted({tool.container_id for tool in self.tools if tool.container_id not in container_ids})
        if missing:
            raise ValueError(f"Configured tool references unknown container(s): {', '.join(missing)}")
        duplicate_containers = _duplicates(container.id for container in self.containers)
        duplicate_tools = _duplicates(tool.id for tool in self.tools)
        if duplicate_containers:
            raise ValueError(f"Duplicate configured container id(s): {', '.join(duplicate_containers)}")
        if duplicate_tools:
            raise ValueError(f"Duplicate configured tool id(s): {', '.join(duplicate_tools)}")
        return self


def _duplicates(values) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text)}


def _tool_search_text(tool: ConfiguredTool, container: ConfiguredContainer | None) -> str:
    parts = [
        tool.id,
        tool.label,
        tool.command,
        tool.description,
        tool.container_id,
        container.label if container else "",
        *(tool.aliases or []),
    ]
    return " ".join(part for part in parts if part)


@lru_cache(maxsize=1)
def load_runtime_tool_config(path: Path = CONFIGURED_TOOLS_PATH) -> RuntimeToolConfig:
    """Return validated configured runtime tools."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Runtime tool config is invalid JSON: {path}") from exc
    try:
        return RuntimeToolConfig.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Runtime tool config is invalid: {exc}") from exc


def configured_containers() -> dict[str, ConfiguredContainer]:
    return {container.id: container for container in load_runtime_tool_config().containers}


def configured_tools() -> list[ConfiguredTool]:
    return list(load_runtime_tool_config().tools)


def search_configured_tools(query: str, *, top_k: int = 5) -> list[tuple[ConfiguredTool, ConfiguredContainer | None, float]]:
    """Rank configured tools with a small lexical scorer."""
    containers = configured_containers()
    query_tokens = _tokens(query)
    rows: list[tuple[ConfiguredTool, ConfiguredContainer | None, float]] = []
    for tool in configured_tools():
        container = containers.get(tool.container_id)
        text = _tool_search_text(tool, container)
        text_tokens = _tokens(text)
        overlap = query_tokens & text_tokens
        exact = query.strip().lower() in {tool.id.lower(), tool.label.lower(), *(alias.lower() for alias in tool.aliases)}
        score = (len(overlap) / max(len(query_tokens), 1)) + (1.0 if exact else 0.0)
        rows.append((tool, container, score))
    rows.sort(key=lambda row: (row[2], row[0].label.lower()), reverse=True)
    return rows[:top_k]


def resolve_configured_tool(container_id: str, tool_id: str) -> tuple[ConfiguredContainer, ConfiguredTool]:
    """Resolve a configured container/tool pair."""
    containers = configured_containers()
    container = containers.get(container_id)
    if container is None:
        raise ValueError(f"Configured container {container_id!r} was not found.")
    for tool in configured_tools():
        if tool.container_id != container.id:
            continue
        names = {tool.id, *tool.aliases}
        if tool_id in names:
            return container, tool
    raise ValueError(f"Configured tool {tool_id!r} was not found in container {container_id!r}.")


def run_analysis_tools_payload() -> list[dict[str, Any]]:
    """Return configured tools visible in the run_analysis UI."""
    containers = configured_containers()
    payload: list[dict[str, Any]] = []
    for tool in configured_tools():
        if not tool.ui.run_analysis:
            continue
        container = containers.get(tool.container_id)
        payload.append(
            {
                "id": tool.id,
                "label": tool.label,
                "description": tool.description,
                "container_id": tool.container_id,
                "container_label": container.label if container else tool.container_id,
            }
        )
    return payload
