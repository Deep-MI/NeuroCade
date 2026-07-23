"""Test assistant runtime behavior for NeuroCade."""

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant import runtime as assistant_runtime_module  # noqa: E402
from api_service.assistant.prompts import build_structured_response_messages, build_system_prompt  # noqa: E402
from api_service.assistant.runtime import AssistantRuntime  # noqa: E402
from api_service.assistant.tools import catalog_execution as assistant_catalog_execution_module  # noqa: E402
from api_service.assistant.tools import workspace_tools as assistant_workspace_tools_module  # noqa: E402
from api_service.assistant.tools.definition import ToolDefinition  # noqa: E402
from api_service.runtime.service import RuntimeService  # noqa: E402
from api_service.runtime_tools.configured_tools import load_runtime_tool_config, run_analysis_tools_payload  # noqa: E402
from api_service.schemas import WorkspaceBatchRunSummary  # noqa: E402
from neurocade_runtime_tools import execution as runtime_execution_module  # noqa: E402

from backend_common import providers as provider_module  # noqa: E402
from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import build_case_id  # noqa: E402
from backend_common.db import AssistantMessage, Base, Case, RoleEnum, User, Workspace, WorkspaceMembership  # noqa: E402


class FakeModel:
    """Stub chat model that returns queued JSON or text responses."""

    def __init__(self, responses: Sequence[dict | str]):
        self._responses = list(responses)

    async def ainvoke(self, _messages):
        payload = self._responses.pop(0)
        if isinstance(payload, str):
            return AIMessage(content=payload)
        return AIMessage(content=json.dumps(payload))


class FakeRuntimeService(RuntimeService):
    """Capture runtime tool listing and execution requests."""

    def __init__(self):
        self.tool_calls = []
        self.tool_queries = []

    async def fetch_tools(self, *, gui_state_key=None, gui_state_override=None):
        self.tool_queries.append(
            {
                "gui_state_key": gui_state_key,
                "gui_state_override": dict(gui_state_override or {}),
            }
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_stats",
                    "description": "Read stats from a processed case.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "case_id": {"type": "string"},
                            "label_query": {"type": "string"},
                        },
                    },
                },
            }
        ]

    async def fetch_gui_state(self, *, gui_state_key=None):
        return {"current_case_id": "ext-case-1", "has_valid_segmentation": True}

    async def call_tool(self, name, arguments, gui_state_override=None, *, gui_state_key=None):
        self.tool_calls.append(
            {
                "name": name,
                "arguments": dict(arguments),
                "gui_state_key": gui_state_key,
                "gui_state_override": dict(gui_state_override or {}),
            }
        )
        case_id = None
        if gui_state_override:
            case_id = gui_state_override.get("current_case_id")
        case_id = arguments.get("case_id") or case_id or "unknown"
        return f"{name} for {case_id}: ok"


@pytest.fixture()
def db_session():
    """Yield an isolated in-memory database session."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def available_provider(monkeypatch):
    """Make the provider registry return an available chat model."""
    def fake_get(role=provider_module.ProviderRole.chat, *args, provider_override=None, model_override=None, **kwargs):
        return provider_module.ModelConfig(
            provider=provider_override or "openai-compatible",
            provider_family="openai_compatible",
            model=model_override or "qwen",
            role=role,
            base_url="https://api.example.invalid",
            available=True,
        )

    monkeypatch.setattr(provider_module.provider_registry, "get", fake_get)


@pytest.fixture()
def seeded_context(db_session, tmp_path, monkeypatch):
    """Create an owned workspace with two cases and a temporary data root."""
    user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
    workspace = Workspace(
        id="workspace-1",
        owner_user_id=user.id,
            name="personal-workspace",
        kind="personal",
        is_default=True,
        status="active",
    )
    case_a = Case(id=build_case_id(workspace.id, "case-a"), workspace_id=workspace.id, owner_user_id=user.id, title="case-a")
    case_b = Case(id=build_case_id(workspace.id, "case-b"), workspace_id=workspace.id, owner_user_id=user.id, title="case-b")
    db_session.add_all([user, workspace, case_a, case_b])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    db_session.commit()
    monkeypatch.setattr(assistant_runtime_module.settings, "fs_data_root", tmp_path / "neurocade-data")
    return db_session, AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"), workspace, case_a, case_b


def _install_managed_bash_image(tmp_path: Path, monkeypatch) -> Path:
    """Configure a managed bash image for tests that inspect direct bash tools."""
    image = tmp_path / "runtime-bash-image-marker"
    image.write_text("docker", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_BASH_IMAGE", "neurocade-runtime-bash:test")
    return image


def test_run_chat_executes_tool_and_streams_events(monkeypatch):
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "reasoning": "I should inspect the stats file first.",
                "tool_calls": [{"name": "read_stats", "arguments": {"case_id": "ext-case-1", "label_query": "Left-Hippocampus"}}],
            },
            {
                "kind": "final",
                "reasoning": "I have the stats I need.",
                "content": "The left hippocampus measurement is available.",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "What is the left hippocampus volume?"}],
            workspace_id=None,
            case_id=None,
            scope="case",
            provider=None,
            model=None,
            event_sink=emit,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "The left hippocampus measurement is available."
    assert any(event_name == "reasoning" for event_name, _payload in events)
    assert any(event_name == "tool_call" and payload["name"] == "read_stats" for event_name, payload in events)


def test_run_chat_uses_explicit_gui_state_override(monkeypatch):
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "reasoning": "I should inspect the active processed case.",
                "tool_calls": [{"name": "read_stats", "arguments": {"label_query": "BrainSegVol"}}],
            },
            {
                "kind": "final",
                "reasoning": "I have the stats I need.",
                "content": "The image resolution is 1.0 mm isotropic.",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime_service = FakeRuntimeService()
    runtime = AssistantRuntime(runtime_service)
    gui_state_override = {
        "current_job_id": "Rhineland_0000",
        "current_workspace_id": "workspace-1",
        "current_case_id": "workspace-1__case-1",
        "loaded_volumes": ["orig.mgz"],
        "has_valid_segmentation": True,
    }
    sanitized_override = {
        "current_job_id": "Rhineland_0000",
        "loaded_volumes": ["orig.mgz"],
        "has_valid_segmentation": True,
        "current_workspace_id": None,
        "current_case_id": None,
    }

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "What is the image resolution?"}],
            workspace_id=None,
            case_id=None,
            scope="case",
            provider=None,
            model=None,
            gui_state_override=gui_state_override,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "The image resolution is 1.0 mm isotropic."
    assert runtime_service.tool_queries == [
        {
            "gui_state_key": None,
            "gui_state_override": sanitized_override,
        }
    ]
    assert runtime_service.tool_calls[0]["gui_state_override"] == sanitized_override


def test_gui_state_override_cannot_retarget_authorized_case(seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    runtime = AssistantRuntime(FakeRuntimeService())

    override = runtime.tools.authorized_gui_state_override(
        {
            "db": db_session,
            "context": context,
            "scope": "case",
            "workspace_id": workspace.id,
            "case_id": case_a.id,
            "gui_state_override": {
                "current_workspace_id": "other-workspace",
                "current_case_id": case_b.id,
                "current_case_output_path": f"workspaces/{workspace.id}/cases/case-b",
                "has_valid_segmentation": True,
            },
        }
    )

    assert override == {
        "has_valid_segmentation": True,
        "current_workspace_id": workspace.id,
        "current_case_id": case_a.id,
    }


def test_system_prompt_exposes_sanitized_gui_state(tmp_path):
    prompt = build_system_prompt(
        tmp_path,
        {
            "scope": "case",
            "workspace_id": "workspace-1",
            "case_id": "workspace-1__case-1",
            "tool_specs": [],
            "gui_state": {
                "is_job_running": False,
                "has_valid_segmentation": True,
                "current_workspace_id": "workspace-1",
                "current_case_id": "workspace-1__case-1",
                "loaded_volumes": ["orig.mgz", "aparc.DKTatlas+aseg.deep.mgz"],
                "loaded_volume_names": ["orig.mgz", "aparc.DKTatlas+aseg.deep.mgz"],
                "visible_volumes": ["orig.mgz"],
                "current_intensity_artifact_id": "artifact-input",
                "current_intensity_volume": "orig.mgz",
                "current_cursor": {
                    "voxel": [12, 34.25, 56],
                    "label_id": 251,
                    "label_name": "CC_Posterior",
                },
            },
        },
    )

    assert '"loaded_volume_names": ["orig.mgz", "aparc.DKTatlas+aseg.deep.mgz"]' in prompt
    assert '"visible_volumes": ["orig.mgz"]' in prompt
    assert '"current_intensity_volume": "orig.mgz"' in prompt
    assert '"current_cursor": {"voxel": [12.0, 34.25, 56.0], "label_id": 251, "label_name": "CC_Posterior"}' in prompt
    assert '"has_active_case": true' in prompt
    assert '"has_loaded_volumes": true' in prompt
    assert '"has_valid_segmentation": true' in prompt
    assert "current_workspace_id" not in prompt
    assert "current_intensity_artifact_id" not in prompt


def test_workspace_system_prompt_says_case_mount_is_unavailable(tmp_path):
    prompt = build_system_prompt(
        tmp_path,
        {
            "scope": "workspace",
            "workspace_id": "workspace-1",
            "case_id": None,
            "tool_specs": [],
            "gui_state": {},
            "workspace_cases": [],
        },
    )

    assert "Workspace chat has no active /case mount" in prompt
    assert "For configured tool questions, use tool_search only" in prompt


def test_catalog_tool_search_returns_configured_tool_payload():
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = json.loads(runtime.tools.catalog_tools.search({"query": "fastsurfer segmentation", "top_k": 1}))

    assert payload == [
        {
            "tool_id": "run_fastsurfer",
            "label": "FastSurfer",
            "container_id": "fastsurfer",
            "container_label": "FastSurfer",
            "command": "/fastsurfer/run_fastsurfer.sh",
            "description": "Run FastSurfer cortical reconstruction and segmentation.",
            "aliases": ["fastsurfer"],
            "score": 1.0,
        }
    ]


def test_catalog_tool_call_delegates_configured_execution_to_runtime_runner(monkeypatch):
    runtime = AssistantRuntime(FakeRuntimeService())
    captured = {}

    from api_service.assistant.tools import catalog_execution as catalog_execution_module
    from neurocade_runtime_tools.execution import RuntimeExecutionResult

    def fake_execute(request, *, db=None):
        captured["request"] = request
        return RuntimeExecutionResult(
            request=request, returncode=0, stdout="ok", stderr="", execution_backend=request.execution_mode
        )

    monkeypatch.setattr(catalog_execution_module, "execute_runtime_request", fake_execute)

    payload = json.loads(
        runtime.tools.catalog_executor.catalog_tool_call(
            {
                "container_id": "fastsurfer",
                "tool_id": "run_fastsurfer",
                "tool_args": ["--help"],
            }
        )
    )

    request = captured["request"]
    assert request.execution_mode == "container"
    assert request.container_run.image == "vnmd/fastsurfer_2.4.2:20260115"
    assert request.container_run.command == ["/fastsurfer/run_fastsurfer.sh", "--help"]
    assert request.container_run.network_disabled is True
    assert str(request.cwd) == str(assistant_runtime_module.ROOT_DIR)
    assert request.timeout_s == 300
    assert payload["returncode"] == 0
    assert payload["stdout"] == "ok"
    assert payload["execution_backend"] == "container"
    assert payload["execution"]["container_id"] == "fastsurfer"
    assert payload["execution"]["tool_id"] == "run_fastsurfer"


def test_run_analysis_tools_come_from_configured_tools():
    load_runtime_tool_config.cache_clear()

    assert run_analysis_tools_payload() == [
        {
            "id": "run_fastsurfer",
            "label": "FastSurfer",
            "description": "Run FastSurfer cortical reconstruction and segmentation.",
            "container_id": "fastsurfer",
            "container_label": "FastSurfer",
        }
    ]


def test_case_catalog_tool_call_passes_artifact_index_target(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeRuntimeService())
    captured = {}

    def fake_execute(request, *, db=None, run_completion_hooks=True):
        captured["request"] = request
        captured["db"] = db
        captured["run_completion_hooks"] = run_completion_hooks
        return runtime_execution_module.RuntimeExecutionResult(request=request, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(assistant_catalog_execution_module, "execute_runtime_request", fake_execute)

    payload = json.loads(
        runtime.tools.catalog_tools.call(
            {
                "db": db_session,
                "context": context,
                "scope": "case",
                "workspace_id": workspace.id,
                "case_id": case_a.id,
            },
            {
                "container_id": "fastsurfer",
                "tool_id": "run_fastsurfer",
                "tool_args": ["--help"],
            },
        )
    )

    request = captured["request"]
    assert payload["returncode"] == 0
    assert captured["db"] is db_session
    assert request.artifact_index_targets
    target = request.artifact_index_targets[0]
    assert target.user_id == case_a.owner_user_id
    assert target.workspace_id == workspace.id
    assert target.case_id == case_a.id
    assert target.case_title == case_a.title
    assert request.container_run is not None
    assert request.container_run.command == ["/fastsurfer/run_fastsurfer.sh", "--help"]


def test_catalog_tool_call_rejects_wrong_container_for_tool():
    runtime = AssistantRuntime(FakeRuntimeService())

    result = runtime.tools.catalog_executor.catalog_tool_call(
        {
            "container_id": "bash_image",
            "tool_id": "run_fastsurfer",
            "tool_args": ["--help"],
        }
    )

    assert "Configured container 'bash_image' was not found" in result


def test_catalog_tool_call_rejects_unknown_tool():
    runtime = AssistantRuntime(FakeRuntimeService())

    result = runtime.tools.catalog_executor.catalog_tool_call(
        {"container_id": "fastsurfer", "tool_id": "unsafe_tool", "tool_args": []}
    )

    assert "Configured tool 'unsafe_tool' was not found in container 'fastsurfer'" in result


def test_run_chat_handles_round_limit(monkeypatch):
    class ToolLoopModel:
        async def ainvoke(self, _messages):
            return AIMessage(
                content=json.dumps(
                    {
                        "kind": "tool_calls",
                        "tool_calls": [{"name": "read_stats", "arguments": {"case_id": "ext-case-1"}}],
                    }
                )
            )

    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: ToolLoopModel())
    monkeypatch.setattr(assistant_runtime_module.settings, "assistant_max_rounds", 1)
    runtime = AssistantRuntime(FakeRuntimeService())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "keep searching"}],
                workspace_id=None,
                case_id=None,
                scope="case",
                provider=None,
                model=None,
                persist=False,
            )
        )

    assert exc_info.value.status_code == 502
    assert "assistant/tool rounds" in exc_info.value.detail


def test_execute_tools_matches_tool_names_case_insensitively():
    runtime = AssistantRuntime(FakeRuntimeService())

    async def execute(arguments):
        return f"read {arguments['path']}"

    result = asyncio.run(
        runtime.loop._execute_tools(
            {
                "conversation": [],
                "tool_calls_log": [],
                "result": {},
                "round_count": 1,
                "pending_tool_calls": [{"name": "read", "arguments": {"path": "/case/stats/aseg.stats"}}],
                "tool_definitions": [
                    ToolDefinition(
                        name="Read",
                        description="Read a file.",
                        parameters={},
                        execute=execute,
                    )
                ],
            }
        )
    )

    assert "error" not in result
    assert result["tool_calls_log"][0]["name"] == "Read"
    assert "read /case/stats/aseg.stats" in result["tool_calls_log"][0]["result"]


def test_execute_tools_reports_unknown_tool():
    runtime = AssistantRuntime(FakeRuntimeService())

    async def read_tool(_arguments: dict[str, object]) -> str:
        return ""

    result = asyncio.run(
        runtime.loop._execute_tools(
            {
                "conversation": [],
                "tool_calls_log": [],
                "result": {},
                "round_count": 8,
                "diagnostic_request_id": "request-1",
                "pending_tool_calls": [{"name": "missing_tool", "arguments": {"x": 1}}],
                "tool_definitions": [
                    ToolDefinition(
                        name="read",
                        description="Read a file.",
                        parameters={},
                        execute=read_tool,
                    )
                ],
            }
        )
    )

    assert result["status"] == "running"
    assert "error" not in result
    assert result["result"]["unknown_tool_call"]["tool"] == "missing_tool"
    assert result["result"]["unknown_tool_call"]["available_tools"] == ["read"]
    assert result["tool_calls_log"][0]["name"] == "missing_tool"
    assert result["tool_calls_log"][0]["result"] == "Error: Unknown tool `missing_tool`. Available tools: read"
    assert result["conversation"][-1]["content"] == "[Tool result] missing_tool: Error: Unknown tool `missing_tool`. Available tools: read"


def test_execute_tools_records_tool_error_result():
    runtime = AssistantRuntime(FakeRuntimeService())

    async def execute(_arguments):
        return "Error: file not found on disk: /case/mri/missing.mgz"

    result = asyncio.run(
        runtime.loop._execute_tools(
            {
                "conversation": [],
                "tool_calls_log": [],
                "result": {},
                "round_count": 5,
                "diagnostic_request_id": "request-2",
                "pending_tool_calls": [{"name": "gui_load_volume", "arguments": {"file_path": "/case/mri/missing.mgz"}}],
                "tool_definitions": [
                    ToolDefinition(
                        name="gui_load_volume",
                        description="Load volume.",
                        parameters={},
                        execute=execute,
                    )
                ],
            }
        )
    )

    assert "error" not in result
    assert result["tool_calls_log"][0]["result"].startswith("Error: file not found")
    assert result["tool_calls_log"][0]["arguments"] == {"file_path": "/case/mri/missing.mgz"}


def test_execute_tools_relays_tool_exceptions_to_model():
    runtime = AssistantRuntime(FakeRuntimeService())
    events = []

    async def execute(_arguments):
        raise HTTPException(status_code=400, detail="/case requires an active case")

    async def emit(event, payload):
        events.append((event, payload))

    result = asyncio.run(
        runtime.loop._execute_tools(
            {
                "conversation": [],
                "tool_calls_log": [],
                "result": {},
                "round_count": 2,
                "diagnostic_request_id": "request-3",
                "event_sink": emit,
                "pending_tool_calls": [{"name": "read", "arguments": {"path": "/case/mri/aseg.mgz"}}],
                "tool_definitions": [
                    ToolDefinition(
                        name="read",
                        description="Read a file.",
                        parameters={},
                        execute=execute,
                    )
                ],
            }
        )
    )

    assert "error" not in result
    assert result["status"] == "running"
    assert result["tool_calls_log"][0]["result"] == "Error: /case requires an active case"
    assert result["conversation"][-1]["content"] == "[Tool result] read: Error: /case requires an active case"
    assert events == [("tool_call", result["tool_calls_log"][0])]


def test_run_chat_persists_history(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    fake_model = FakeModel([{"kind": "final", "reasoning": "No tools needed.", "content": "Ready."}])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Hello assistant"}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            scope="case",
            provider=None,
            model=None,
            persist=True,
        )
    )
    history = asyncio.run(runtime.list_history(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))

    assert history[0].role == "user"
    assert history[-1].role == "assistant"
    assert history[-1].content == "Ready."
    thread_key = asyncio.run(runtime.get_thread_key(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))
    assert thread_key == f"case:{case_a.id}"


def test_run_chat_streams_and_persists_interim_assistant_message(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "reasoning": "The first route failed, so I will try a fallback.",
                "message": "The container route failed. I'll try reading the stats directly instead.",
                "tool_calls": [{"name": "read_stats", "arguments": {"case_id": case_a.id, "label_query": "Left-Hippocampus"}}],
            },
            {
                "kind": "final",
                "reasoning": "The fallback worked.",
                "content": "The fallback completed.",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Try a fallback after a tool failure."}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            scope="case",
            provider=None,
            model=None,
            event_sink=emit,
            persist=True,
        )
    )

    assert [event for event, _payload in events] == ["assistant_message", "reasoning", "tool_call", "reasoning"]
    assert events[0][1]["content"] == "The container route failed. I'll try reading the stats directly instead."

    history = asyncio.run(runtime.list_history(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))
    assert [message.role for message in history] == ["user", "assistant", "tool-calls", "assistant"]
    assert history[1].content == "The container route failed. I'll try reading the stats directly instead."
    assert history[-1].content == "The fallback completed."


def test_run_chat_continues_after_unknown_tool(monkeypatch, seeded_context, tmp_path):
    db_session, context, workspace, case_a, _case_b = seeded_context
    log_path = tmp_path / "assistant-conversations.jsonl"
    monkeypatch.setenv("NEUROCADE_ASSISTANT_CONVERSATION_LOG", str(log_path))
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "reasoning": "I should inspect a file.",
                "tool_calls": [{"name": "not_a_tool", "arguments": {"path": "/case/stats/aseg.stats"}}],
            },
            {
                "kind": "final",
                "reasoning": "The requested tool name was invalid, so I should recover.",
                "content": "I could not use that tool name. I can retry with one of the available tools.",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Read the stats file"}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            scope="case",
            provider=None,
            model=None,
            diagnostic_request_id="request-unknown-tool",
            persist=True,
        )
    )

    assert payload["message"]["content"].startswith("I could not use that tool name")
    assert payload["tool_calls_log"][0]["name"] == "not_a_tool"
    assert payload["tool_calls_log"][0]["result"].startswith("Error: Unknown tool `not_a_tool`")
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["event"] == "assistant.turn.persisted"
    assert records[0]["thread_key"] == f"case:{case_a.id}"
    assert records[0]["messages"][0]["content_json"]["value"] == "Read the stats file"
    assert records[0]["messages"][1]["metadata_json"]["toolCalls"][0]["name"] == "not_a_tool"


def test_run_chat_continues_after_recoverable_tool_exception(monkeypatch, seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "reasoning": "I should inspect the current case.",
                "tool_calls": [{"name": "read", "arguments": {"path": "/case/mri/aseg.mgz"}}],
            },
            {
                "kind": "final",
                "reasoning": "There is no active case, so I should recover with guidance.",
                "content": "I cannot inspect /case from workspace chat. Open a case first, or provide a case ID.",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Check the current case segmentation output"}],
            workspace_id=workspace.id,
            case_id=None,
            scope="workspace",
            provider=None,
            model=None,
            persist=True,
        )
    )

    assert payload["message"]["content"].startswith("I cannot inspect /case")
    assert payload["tool_calls_log"][0]["name"] == "read"
    assert payload["tool_calls_log"][0]["result"] == "Error: /case requires an active case"

    history = asyncio.run(runtime.list_history(db_session, context, scope="workspace", workspace_id=workspace.id))
    assert [message.role for message in history][-3:] == ["user", "tool-calls", "assistant"]
    assert history[-2].toolCalls[0].result == "Error: /case requires an active case"


def test_clear_history_removes_persisted_messages(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    fake_model = FakeModel([{"kind": "final", "reasoning": "No tools needed.", "content": "Ready."}])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Hello assistant"}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            scope="case",
            provider=None,
            model=None,
            persist=True,
        )
    )

    assert db_session.query(AssistantMessage).count() > 0
    asyncio.run(runtime.clear_history(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))

    history = asyncio.run(runtime.list_history(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))
    assert history == []
    assert db_session.query(AssistantMessage).count() == 0


def test_workspace_batch_bash_tool_queues_workspace_run(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    fake_model = FakeModel([])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    monkeypatch.setattr(
        assistant_workspace_tools_module,
        "create_workspace_batch_run",
        lambda *args, **kwargs: WorkspaceBatchRunSummary(
            run_id="wf-batch-1",
            workspace_id=workspace.id,
            status="queued",
            run_type="workspace_batch_bash",
            command="mri_synthstrip --help | head",
            report_name="workspace-batch",
            analysis_id="ws-analysis-1",
            selected_case_count=2,
            total_cases=2,
            queued_cases=2,
            running_cases=0,
            completed_cases=0,
            failed_cases=0,
            canceled_cases=0,
            external_task_id=None,
            artifact_count=3,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        ),
    )
    runtime = AssistantRuntime(FakeRuntimeService())

    queued = runtime.tools.workspace_tools.batch_bash(
        {
            "db": db_session,
            "context": context,
            "workspace_id": workspace.id,
            "provider_name": "openai-compatible",
            "model_name": "qwen",
        },
        {
            "command": "mri_synthstrip --help | head",
            "case_ids": [case_a.id, case_b.id],
            "report_name": "hippocampus-summary",
        },
    )

    assert "Queued workspace batch run `wf-batch-1`" in queued
    assert "first case is running as a probe" in queued


def test_workspace_bash_tool_queues_workspace_wide_run(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    fake_model = FakeModel([])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    monkeypatch.setattr(
        assistant_workspace_tools_module,
        "create_workspace_command_run",
        lambda *args, **kwargs: WorkspaceBatchRunSummary(
            run_id="wf-workspace-1",
            workspace_id=workspace.id,
            status="queued",
            run_type="workspace_bash",
            command="find /cases -maxdepth 2 -type f > /workspace/files.txt",
            report_name="workspace-summary",
            analysis_id="ws-analysis-2",
            selected_case_count=2,
            total_cases=2,
            queued_cases=0,
            running_cases=0,
            completed_cases=0,
            failed_cases=0,
            canceled_cases=0,
            external_task_id="task-workspace-1",
            artifact_count=3,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        ),
    )
    runtime = AssistantRuntime(FakeRuntimeService())

    queued = runtime.tools.workspace_tools.bash(
        {
            "db": db_session,
            "context": context,
            "workspace_id": workspace.id,
            "provider_name": "openai-compatible",
            "model_name": "qwen",
        },
        {
            "command": "find /cases -maxdepth 2 -type f > /workspace/files.txt",
            "case_ids": [case_a.id, case_b.id],
            "report_name": "workspace-summary",
        },
    )

    assert "Queued workspace-wide run `wf-workspace-1`" in queued
    assert "/cases/<case-slug>/" in queued


def test_workspace_tools_are_workspace_specific(monkeypatch, tmp_path, seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    _install_managed_bash_image(tmp_path, monkeypatch)
    fake_model = FakeModel([])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    _definitions, tools = asyncio.run(
        runtime.tools.build(
            {
                "db": db_session,
                "context": context,
                "scope": "workspace",
                "workspace_id": workspace.id,
            }
        )
    )
    tool_names = [tool["function"]["name"] for tool in tools]

    assert "workspace_batch_bash" in tool_names
    assert "workspace_bash" in tool_names
    assert "workspace_probe_bash" in tool_names
    assert "workspace_case_file_tree" in tool_names
    assert "workspace_file_tree" in tool_names
    assert "workspace_cancel_batch_run" in tool_names
    assert "workspace_batch_tool" not in tool_names

    result = runtime.tools.workspace_tools.list_batch_runs(
        {
            "db": db_session,
            "context": context,
            "workspace_id": workspace.id,
        }
    )

    assert result == "[]"


def test_workspace_file_tools_do_not_advertise_case_mount(seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeRuntimeService())

    _definitions, tools = asyncio.run(
        runtime.tools.build(
            {
                "db": db_session,
                "context": context,
                "scope": "workspace",
                "workspace_id": workspace.id,
            }
        )
    )
    read_tool = next(tool for tool in tools if tool["function"]["name"] == "read")
    description = read_tool["function"]["description"]
    path_description = read_tool["function"]["parameters"]["properties"]["path"]["description"]

    assert "/case is not available in workspace chat" in description
    assert "/case is not available in workspace chat" in path_description
    assert "active case mount at /case" not in description


def test_workspace_tools_expose_bash_with_configured_docker_image(monkeypatch, tmp_path, seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    monkeypatch.setenv("NEUROCADE_BASH_IMAGE", "neurocade-runtime-bash:test")
    runtime = AssistantRuntime(FakeRuntimeService())

    _definitions, tools = asyncio.run(
        runtime.tools.build(
            {
                "db": db_session,
                "context": context,
                "scope": "workspace",
                "workspace_id": workspace.id,
            }
        )
    )
    tool_names = {tool["function"]["name"] for tool in tools}

    assert "workspace_batch_bash" in tool_names
    assert "workspace_bash" in tool_names
    assert "workspace_probe_bash" in tool_names
    assert "workspace_case_file_tree" in tool_names
    assert "workspace_file_tree" in tool_names


def test_catalog_tools_are_local_pydantic_tools():
    runtime = AssistantRuntime(FakeRuntimeService())

    tool_specs = [tool.as_openai_tool() for tool in runtime.tools.catalog_tools.build_tools({"scope": "case"})]
    tool_names = {tool["function"]["name"] for tool in tool_specs}

    assert tool_names == {"tool_search", "tool_call"}
    tool_call = next(tool for tool in tool_specs if tool["function"]["name"] == "tool_call")
    assert "execute" not in tool_call["function"]["parameters"]["properties"]


def test_case_scope_includes_python_and_bash_tools_when_bash_image_installed(monkeypatch, tmp_path, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    _install_managed_bash_image(tmp_path, monkeypatch)
    runtime = AssistantRuntime(FakeRuntimeService())

    _definitions, tool_specs = asyncio.run(
        runtime.tools.build(
            {
                "db": db_session,
                "context": context,
                "scope": "case",
                "workspace_id": workspace.id,
                "case_id": case_a.id,
            }
        )
    )
    tool_names = {tool["function"]["name"] for tool in tool_specs}

    assert "python_run" in tool_names
    assert "bash" in tool_names
    assert "read" in tool_names
    assert "write" in tool_names
    assert "edit" in tool_names
    assert "Python_run" not in tool_names
    assert "Bash" not in tool_names


def test_case_scope_exposes_python_and_bash_tools_with_default_docker_image(monkeypatch, tmp_path, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    monkeypatch.delenv("NEUROCADE_BASH_IMAGE", raising=False)
    runtime = AssistantRuntime(FakeRuntimeService())

    _definitions, tool_specs = asyncio.run(
        runtime.tools.build(
            {
                "db": db_session,
                "context": context,
                "scope": "case",
                "workspace_id": workspace.id,
                "case_id": case_a.id,
            }
        )
    )
    tool_names = {tool["function"]["name"] for tool in tool_specs}

    assert "python_run" in tool_names
    assert "bash" in tool_names
    assert "tool_search" in tool_names
    assert "tool_call" in tool_names


def test_case_python_run_uses_managed_bash_image_and_case_mount(monkeypatch, tmp_path, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    _install_managed_bash_image(tmp_path, monkeypatch)
    runtime = AssistantRuntime(FakeRuntimeService())
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
    }
    case_dir = runtime.tools.case_tools.active_case_dir(state)
    scripts_dir = case_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "demo.py").write_text("print('ok')\n", encoding="utf-8")
    captured = {}

    def fake_execute(request, *, db=None, run_completion_hooks=True):
        captured["request"] = request
        captured["db"] = db
        captured["run_completion_hooks"] = run_completion_hooks
        return runtime_execution_module.RuntimeExecutionResult(
            request=request,
            returncode=0,
            stdout="ok\n",
            stderr="",
            execution_backend="test",
        )

    monkeypatch.setattr(assistant_catalog_execution_module, "execute_runtime_request", fake_execute)

    result = runtime.tools.case_tools.case_python_run_tool(state, {"script_path": "/case/scripts/demo.py", "args": ["--flag"]})

    assert json.loads(result)["stdout"] == "ok\n"
    request = captured["request"]
    assert request.container_run is not None
    assert any(
        str(bind.host_path) == str(case_dir.resolve()) and bind.container_path == "/case" and bind.mode == "rw"
        for bind in request.container_run.binds
    )
    assert "python3.12 /case/scripts/demo.py --flag" in request.container_run.command[-1]
    assert request.timeout_s == 300
    assert captured["db"] is db_session
    assert request.artifact_index_targets
    assert request.artifact_index_targets[0].case_id == case_a.id


def test_case_bash_passes_artifact_index_target(monkeypatch, tmp_path, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    _install_managed_bash_image(tmp_path, monkeypatch)
    runtime = AssistantRuntime(FakeRuntimeService())
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
    }
    captured = {}

    def fake_execute(request, *, db=None, run_completion_hooks=True):
        captured["request"] = request
        captured["db"] = db
        return runtime_execution_module.RuntimeExecutionResult(request=request, returncode=1, stdout="", stderr="failed\n")

    monkeypatch.setattr(assistant_catalog_execution_module, "execute_runtime_request", fake_execute)

    result = runtime.tools.case_tools.case_bash_tool(state, {"command": "touch /case/qc.txt && false"})

    request = captured["request"]
    assert result.startswith("Error: bash exited with code 1.")
    assert captured["db"] is db_session
    assert request.artifact_index_targets
    assert request.artifact_index_targets[0].case_id == case_a.id
    assert request.container_run is not None
    assert "touch /case/qc.txt && false" in request.container_run.command[-1]


def test_catalog_tool_binds_active_case_without_output_alias(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    fs_root = assistant_runtime_module.settings.fs_data_root
    monkeypatch.setattr(assistant_runtime_module.settings, "outputs_dir_override", fs_root / "output")
    runtime = AssistantRuntime(FakeRuntimeService())

    binds = runtime.tools.catalog_executor.catalog_runtime_binds(
        {
            "db": db_session,
            "context": context,
            "scope": "case",
            "workspace_id": workspace.id,
            "case_id": case_a.id,
        }
    )
    assert not any(bind.container_path in {"/data", "/output"} for bind in binds)
    assert any(bind.container_path == "/case" and bind.mode == "rw" for bind in binds)


def test_catalog_tool_binds_workspace_output_for_workspace_scope(monkeypatch, seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    fs_root = assistant_runtime_module.settings.fs_data_root
    monkeypatch.setattr(assistant_runtime_module.settings, "outputs_dir_override", fs_root / "output")
    runtime = AssistantRuntime(FakeRuntimeService())

    binds = runtime.tools.catalog_executor.catalog_runtime_binds(
        {
            "db": db_session,
            "context": context,
            "scope": "workspace",
            "workspace_id": workspace.id,
        }
    )
    assert [(bind.host_path, bind.container_path, bind.mode) for bind in binds] == [
        ((fs_root / "output" / "workspaces" / workspace.id).resolve(), "/workspace", "rw")
    ]


def test_catalog_public_execution_hides_container_image_reference():
    runtime = AssistantRuntime(FakeRuntimeService())
    config = load_runtime_tool_config()

    public_execution = runtime.tools.catalog_executor.public_catalog_execution(
        config.containers[0],
        config.tools[0],
        "fastsurfer",
        ["--help"],
    )

    assert "image_path" not in public_execution
    assert "image" not in public_execution
    assert public_execution["container_id"] == "fastsurfer"
    assert public_execution["tool_id"] == "run_fastsurfer"
    assert public_execution["command"] == ["/fastsurfer/run_fastsurfer.sh", "--help"]


def test_workspace_list_cases_tool_queries_live_db_state(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    fake_model = FakeModel([])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    result = runtime.tools.workspace_tools.list_cases(
        {
            "db": db_session,
            "context": context,
            "workspace_id": workspace.id,
            "workspace_cases": [],
        }
    )
    parsed = json.loads(result)

    assert {entry["case_id"] for entry in parsed} == {case_a.id, case_b.id}
    assert all(entry["workspace_mount_path"].startswith("/cases/") for entry in parsed)


def test_build_structured_response_messages_keeps_only_the_initial_system_message():
    messages = build_structured_response_messages(
        "system prompt",
        [
            {"role": "user", "content": "List structural images."},
            {"role": "system", "content": "[Tool result] workspace_list_cases: []"},
            {"role": "assistant", "content": '{"kind":"tool_calls","tool_calls":[]}'},
        ],
    )

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert isinstance(messages[2], HumanMessage)
    assert isinstance(messages[3], AIMessage)


def test_run_chat_repairs_non_json_structured_response(monkeypatch):
    fake_model = FakeModel(
        [
            {"not_valid": "structured_response"},
            {
                "kind": "final",
                "reasoning": "Repairing the structured response output.",
                "content": "ready",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "Reply with the single word ready."}],
            workspace_id=None,
            case_id=None,
            scope="workspace",
            provider=None,
            model=None,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "ready"


def test_run_chat_allows_final_answer_after_last_tool_round(monkeypatch):
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "reasoning": f"Tool round {index}",
                "tool_calls": [{"name": "read_stats", "arguments": {"case_id": "ext-case-1", "label_query": f"Label {index}"}}],
            }
            for index in range(1, 6)
        ]
        + [
            {
                "kind": "final",
                "reasoning": "All tool work is done.",
                "content": "finished",
            }
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "Use tools until you are done."}],
            workspace_id=None,
            case_id=None,
            scope="case",
            provider=None,
            model=None,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "finished"
    assert len(payload["tool_calls_log"]) == 5


def test_run_chat_repairs_empty_final_response(monkeypatch):
    fake_model = FakeModel(
        [
            {"kind": "final", "reasoning": "I know what to do next.", "content": ""},
            {
                "kind": "final",
                "reasoning": "The previous response was empty.",
                "content": "I could not complete the tool step, but I can explain what happened.",
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "Explain the failed tool call."}],
            workspace_id=None,
            case_id=None,
            scope="case",
            provider=None,
            model=None,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "I could not complete the tool step, but I can explain what happened."


def test_run_chat_repairs_empty_tool_call_list(monkeypatch):
    fake_model = FakeModel(
        [
            {"kind": "tool_calls", "reasoning": "I should use a tool.", "tool_calls": []},
            {"kind": "tool_calls", "reasoning": "Now I will use the tool.", "tool_calls": [{"name": "read_stats", "arguments": {}}]},
            {"kind": "final", "reasoning": "Tool execution completed.", "content": "done"},
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "Read stats."}],
            workspace_id=None,
            case_id=None,
            scope="case",
            provider=None,
            model=None,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "done"
    assert len(payload["tool_calls_log"]) == 1


def test_run_chat_rejects_plain_text_when_repair_still_is_not_json(monkeypatch):
    class PlainTextModel:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="Thinking Process:\n\n1. Consider the request.\n\nready")
            return AIMessage(content="still not json")

    fake_model = PlainTextModel()
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "Reply with the single word ready."}],
                workspace_id=None,
                case_id=None,
                scope="workspace",
                provider=None,
                model=None,
                persist=False,
            )
        )

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "Assistant model did not return a usable JSON response"


def test_run_chat_reports_unconfigured_provider_before_model_call(monkeypatch):
    unavailable = provider_module.ModelConfig(
        provider="openai-compatible",
        provider_family="openai_compatible",
        model="qwen",
        role=provider_module.ProviderRole.chat,
        base_url="https://api.example.invalid",
        available=False,
        availability_reason="LLM_BACKEND_API_KEY is not configured",
    )
    monkeypatch.setattr(provider_module.provider_registry, "get", lambda *args, **kwargs: unavailable)
    runtime = AssistantRuntime(FakeRuntimeService())

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "Hello"}],
                workspace_id=None,
                case_id=None,
                scope="workspace",
                provider=None,
                model=None,
                persist=False,
            )
        )

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "Model provider 'openai-compatible' is not configured: LLM_BACKEND_API_KEY is not configured"


def test_run_chat_rejects_inaccessible_workspace(monkeypatch, seeded_context):
    db_session, _context, _workspace, case_a, _case_b = seeded_context
    fake_model = FakeModel([{"kind": "final", "reasoning": "No tools needed.", "content": "Ready."}])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeRuntimeService())
    other_user = User(id="user-2", external_auth_id="user-2", email="other@example.com", full_name="Other User")
    db_session.add_all([other_user])
    db_session.commit()
    other_context = AuthContext(user=other_user, role=RoleEnum.owner, auth_mode="local")

    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            runtime.run_chat(
                db=db_session,
                context=other_context,
                messages=[{"role": "user", "content": "Hello assistant"}],
                workspace_id=case_a.workspace_id,
                case_id=case_a.id,
                scope="case",
                provider=None,
                model=None,
                persist=True,
            )
        )

    assert "Workspace not found" in str(excinfo.value)
