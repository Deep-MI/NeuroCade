"""Provide API service assistant prompts behavior for NeuroCade."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from backend_common.db import AssistantScope


def load_text(path: Path) -> str:
    """Read a UTF-8 prompt fragment, returning an empty string when absent.

    Parameters
    ----------
    path : Path
        Prompt fragment file to load.

    Returns
    -------
    str
        Stripped file contents, or an empty string if the file is missing.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def stringify_content(content: Any) -> str:
    """Extract plain text from chat message content.

    Parameters
    ----------
    content : Any
        Message content as a string, OpenAI-style content parts, or another value.

    Returns
    -------
    str
        Text suitable for passing back to the assistant model.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
        return "\n".join(part for part in chunks if part)
    return str(content or "")


def build_structured_response_messages(system_prompt: str, conversation: list[dict[str, Any]]) -> list[BaseMessage]:
    """Convert conversation history into LangChain messages for structured JSON responses.

    Parameters
    ----------
    system_prompt : str
        Canonical system instruction to keep as the first message.
    conversation : list[dict[str, Any]]
        Prior chat turns and tool context from the request state.

    Returns
    -------
    list[BaseMessage]
        Messages ending with the required JSON response schema.
    """
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    for item in conversation:
        role = item.get("role")
        content = item.get("content")
        text = stringify_content(content)
        if not text:
            continue
        if role == "assistant":
            messages.append(AIMessage(content=text))
        else:
            # Some OpenAI-compatible backends reject any system message after the first one.
            # Keep the canonical assistant instruction as the only system message and feed
            # tool results / other context back as ordinary follow-up turns.
            messages.append(HumanMessage(content=text))
    messages.append(
        HumanMessage(
            content=(
                "Return only JSON using this schema: "
                '{"kind":"final","reasoning":"short optional string","content":"answer"} '
                'or {"kind":"tool_calls","reasoning":"short optional string","message":"optional user-facing progress update","tool_calls":[{"name":"tool_name","arguments":{}}]}. '
                "Use message only when you need to briefly tell the user what happened before continuing with tools. "
                "Do not wrap the JSON in markdown."
            )
        )
    )
    return messages


def prompt_gui_state(gui_state: Mapping[str, Any]) -> dict[str, Any]:
    """Build the compact GUI context exposed to the assistant prompt.

    Parameters
    ----------
    gui_state : Mapping[str, Any]
        Raw frontend state for the active workspace or case.

    Returns
    -------
    dict[str, Any]
        Sanitized GUI state with current case, volumes, running status, and cursor data.
    """
    loaded_volume_names = gui_state.get("loaded_volume_names") or gui_state.get("loaded_volumes") or []
    visible_volumes = gui_state.get("visible_volumes") or []
    current_intensity_volume = gui_state.get("current_intensity_volume")
    current_cursor = gui_state.get("current_cursor") if isinstance(gui_state.get("current_cursor"), Mapping) else None
    cursor_payload = None
    if current_cursor:
        voxel = current_cursor.get("voxel")
        label_id = current_cursor.get("label_id")
        label_name = current_cursor.get("label_name")
        if (
            isinstance(voxel, list)
            and len(voxel) == 3
            and all(isinstance(value, (int, float)) for value in voxel)
        ):
            cursor_payload = {
                "voxel": [round(float(value), 3) for value in voxel],
                "label_id": label_id if isinstance(label_id, int) else None,
                "label_name": str(label_name)[:120] if isinstance(label_name, str) else None,
            }
    return {
        "is_job_running": bool(gui_state.get("is_job_running", False)),
        "has_active_case": bool(gui_state.get("current_case_id")),
        "has_loaded_volumes": bool(loaded_volume_names),
        "has_valid_segmentation": bool(gui_state.get("has_valid_segmentation", False)),
        "current_case_id": gui_state.get("current_case_id"),
        "loaded_volume_names": [
            str(volume_name)
            for volume_name in loaded_volume_names
            if isinstance(volume_name, str) and volume_name
        ],
        "visible_volumes": [
            str(volume_name)
            for volume_name in visible_volumes
            if isinstance(volume_name, str) and volume_name
        ],
        "current_intensity_volume": str(current_intensity_volume)[:255] if isinstance(current_intensity_volume, str) else None,
        "current_cursor": cursor_payload,
    }


def build_system_prompt(
    config_dir: Path,
    state: Mapping[str, Any],
    *,
    info_limit: int = 2500,
) -> str:
    """Assemble the assistant system prompt from config, tools, and session state.

    Parameters
    ----------
    config_dir : Path
        Directory containing SOUL.md, INFORMATION.md, and RULES.md.
    state : Mapping[str, Any]
        Assistant runtime state, including scope, tools, GUI state, and workspace data.
    info_limit : int
        Maximum number of INFORMATION.md characters to include.

    Returns
    -------
    str
        Complete system prompt for the assistant model.
    """
    soul = load_text(config_dir / "SOUL.md")
    info = load_text(config_dir / "INFORMATION.md")
    rules = load_text(config_dir / "RULES.md")
    scope = state["scope"]
    tool_blocks = []
    tool_names = set()
    for tool in state.get("tool_specs", []):
        function = tool.get("function", {})
        name = str(function.get("name") or "")
        if name:
            tool_names.add(name)
        tool_blocks.append(
            f"- {function.get('name')}: {function.get('description', '')}\n"
            f"  Parameters JSON schema: {json.dumps(function.get('parameters', {}), ensure_ascii=True)}"
        )
    session_lines = [
        f"Scope: {scope}",
        f"Workspace ID: {state.get('workspace_id')}",
    ]
    if state.get("case_id"):
        session_lines.append(f"Case ID: {state.get('case_id')}")
    gui_state = state.get("gui_state", {})
    if gui_state:
        llm_gui_state = prompt_gui_state(gui_state)
        session_lines.append(f"GUI state: {json.dumps(llm_gui_state, ensure_ascii=True)}")
        current_case_id = gui_state.get("current_case_id")
        loaded_volumes = llm_gui_state.get("loaded_volume_names") or []
        visible_volumes = llm_gui_state.get("visible_volumes") or []
        current_intensity_volume = llm_gui_state.get("current_intensity_volume")
        if current_case_id:
            session_lines.append("Current case directory for runtime tools: /case")
        if loaded_volumes:
            session_lines.append(
                "Loaded volume display filenames: "
                + json.dumps(loaded_volumes, ensure_ascii=True)
            )
            session_lines.append(
                "Loaded volume path rule: these are display filenames, not guaranteed direct /case paths. "
                "If an exact runtime path is needed, inspect the case tree; FastSurfer volumes usually live under /case/mri/."
            )
        if visible_volumes:
            session_lines.append(
                "Currently visible volume display filenames: "
                + json.dumps(visible_volumes, ensure_ascii=True)
            )
        if current_intensity_volume:
            session_lines.append(f"Current FastSurfer input volume: {current_intensity_volume}")
        session_lines.append(
            "GUI tool rule: case-mode GUI tools stay registered even when the current viewer state "
            "does not satisfy their preconditions. If a GUI tool returns an Error, use the GUI state "
            "summary and the error text to choose the next step."
        )
        session_lines.append(
            "Path rule: in case mode the active case is mounted directly at /case. Prefer /case/... "
            "paths for current-case command inputs and outputs, such as /case/mri/result.mgz. "
            "If you need to discover exact filenames or folders, "
            "use the case_file_tree tool."
        )
        session_lines.append(
            "Configured tool rule: use tool_search before tool_call. tool_search returns only tools "
            "configured by NeuroCade and includes the container_id and tool_id required by tool_call. "
            "All current-case file arguments must use explicit /case/... paths."
        )
        if {"python_run", "bash"}.issubset(tool_names):
            session_lines.append(
                "Python/Bash rule: for custom case-local scripts, use write to create files under /case, "
                "then python_run to execute an existing script. Use bash for shell operations that are not "
                "covered by configured neuroimaging tools."
            )
    if scope == AssistantScope.workspace.value:
        session_lines.append(
            "Workspace chat has no active /case mount. For configured tool questions, use tool_search only. "
            "To inspect case files from workspace chat, use workspace_case_file_tree with an explicit case_id, "
            "or ask the user to open/select a case first."
        )
    if scope == AssistantScope.workspace.value and state.get("workspace_cases"):
        session_lines.append(f"Workspace cases: {json.dumps(state['workspace_cases'], ensure_ascii=True)}")
        session_lines.append(
            "Workspace-mode path rule: one-shot workspace-wide commands use read-only case mounts under "
            "/cases/<case-slug>/ and a dedicated writable /workspace/ analysis folder. Per-case fan-out batch "
            "commands still mount one selected case directly at /case."
        )
        workspace_rule = (
            "Workspace-mode tool rule: do not use case-mode GUI tools here. "
            "Use workspace_file_tree to inspect the workspace-wide /cases and /workspace layout."
        )
        if {"workspace_probe_bash", "workspace_bash", "workspace_batch_bash"}.issubset(tool_names):
            workspace_rule += (
                " For generic commands, inspect help first with workspace_probe_bash using `<cmd> --help | head`. "
                "Use workspace_bash for one command that reads across multiple cases and writes a report into /workspace/. "
                "Use workspace_batch_bash only when the same command should run separately for each case using /case."
            )
        session_lines.append(workspace_rule)
    return "\n\n".join(
        part
        for part in [
            soul or "You are the FastSurfer neuroimaging assistant.",
            "Use the available tools instead of describing commands manually.",
            "If the task requires a tool, respond with JSON tool_calls. If not, respond with JSON final content. "
            "When continuing after a recoverable tool issue, include a concise user-facing message in the tool_calls response.",
            "Available tools:\n" + "\n".join(tool_blocks),
            "Session:\n" + "\n".join(session_lines),
            info[:info_limit] if info else "",
            rules,
        ]
        if part
    )
