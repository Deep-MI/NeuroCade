"""Test assistant runtime behavior for NeuroCade."""

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant import runtime as assistant_runtime_module  # noqa: E402
from api_service.assistant.prompts import build_model_messages, build_system_prompt, load_text  # noqa: E402
from api_service.assistant.runtime import AssistantRuntime  # noqa: E402
from api_service.assistant.tools import probe_tools as probe_tools_module  # noqa: E402
from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult, ToolRisk  # noqa: E402
from api_service.runtime.gui_runtime import GuiRuntime  # noqa: E402
from api_service.runtime_tools.workflow_catalog import load_workflow_catalog, run_analysis_workflows_payload  # noqa: E402

from backend_common import providers as provider_module  # noqa: E402
from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import ensure_case_storage_layout  # noqa: E402
from backend_common.db import (  # noqa: E402
    AssistantMessage,
    AssistantTurn,
    Base,
    Case,
    RoleEnum,
    Run,
    RunStatus,
    User,
    Workspace,
    WorkspaceMembership,
)


def write_prompt_config(path: Path, *, information: str = "System information") -> None:
    """Create the three required assistant prompt fragments for isolated tests."""
    path.joinpath("SOUL.md").write_text("Assistant role", encoding="utf-8")
    path.joinpath("INFORMATION.md").write_text(information, encoding="utf-8")
    path.joinpath("RULES.md").write_text("Operating rules", encoding="utf-8")


class FakeModel:
    """Stub chat model that returns queued native provider responses."""

    def __init__(self, responses: Sequence[dict | str]):
        self._responses = list(responses)
        self._call_index = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages):
        payload = self._responses.pop(0)
        if isinstance(payload, str):
            return AIMessage(content=payload)
        reasoning = payload.get("reasoning")
        additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        if payload.get("kind") == "final":
            return AIMessage(content=payload.get("content", ""), additional_kwargs=additional_kwargs)
        if payload.get("kind") == "tool_calls":
            tool_calls = []
            for call in payload.get("tool_calls", []):
                self._call_index += 1
                tool_calls.append({
                    "id": f"call-{self._call_index}",
                    "name": call["name"],
                    "args": call.get("arguments", {}),
                })
            return AIMessage(
                content=payload.get("message", ""),
                tool_calls=tool_calls,
                additional_kwargs=additional_kwargs,
            )
        return AIMessage(content=json.dumps(payload))


class FakeGuiRuntime(GuiRuntime):
    """Capture runtime tool listing and execution requests."""

    def __init__(self):
        self.tool_calls = []
        self.tool_queries = []

    def available_tools(self, *, gui_state_key=None, gui_state_override=None):
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

    def gui_state(self, *, gui_state_key=None):
        return {
            "case_id": "ext-case-1",
            "layers": [
                {
                    "id": "segmentation:aseg",
                    "filename": "aparc.DKTatlas+aseg.deep.mgz",
                    "type": "segmentation",
                    "role": "segmentation",
                    "visible": True,
                }
            ],
        }

    def call_tool(self, name, arguments, gui_state_override=None, *, gui_state_key=None):
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
            case_id = gui_state_override.get("case_id")
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
    def fake_get(*args, provider_override=None, model_override=None, **kwargs):
        return provider_module.ModelConfig(
            provider=provider_override or "openai-compatible",
            provider_family="openai_compatible",
            model=model_override or "qwen",
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
    )
    case_a = Case(id="case-a-id", workspace_id=workspace.id, owner_user_id=user.id, title="case-a")
    case_b = Case(id="case-b-id", workspace_id=workspace.id, owner_user_id=user.id, title="case-b")
    db_session.add_all([user, workspace, case_a, case_b])
    db_session.flush()
    db_session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.owner, granted_by_user_id=user.id))
    db_session.commit()
    monkeypatch.setattr(assistant_runtime_module.settings, "fs_data_root", tmp_path / "neurocade-data")
    ensure_case_storage_layout(assistant_runtime_module.settings, case_a, workspace)
    ensure_case_storage_layout(assistant_runtime_module.settings, case_b, workspace)
    return db_session, AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"), workspace, case_a, case_b


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
    runtime = AssistantRuntime(FakeGuiRuntime())
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
            gui_session_id="gui-test",
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
    gui_runtime = FakeGuiRuntime()
    runtime = AssistantRuntime(gui_runtime)
    gui_state_override = {
        "current_job_id": "Rhineland_0000",
        "workspace_id": "workspace-1",
        "case_id": "case-1-id",
        "layers": [
            {
                "id": "intensity:orig",
                "filename": "orig.mgz",
                "type": "intensity",
                "role": "intensity",
                "visible": True,
            }
        ],
    }
    sanitized_override = {
        "layers": [
            {
                "id": "intensity:orig",
                "filename": "orig.mgz",
                "type": "intensity",
                "role": "intensity",
                "visible": True,
            }
        ],
        "workspace_id": None,
        "case_id": None,
    }

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "What is the image resolution?"}],
            workspace_id=None,
            case_id=None,
            gui_session_id="gui-test",
            scope="case",
            provider=None,
            model=None,
            gui_state_override=gui_state_override,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "The image resolution is 1.0 mm isotropic."
    assert gui_runtime.tool_queries == [
        {
            "gui_state_key": "user:ephemeral|workspace:ephemeral|case:-|session:gui-test",
            "gui_state_override": sanitized_override,
        }
    ]
    assert gui_runtime.tool_calls[0]["gui_state_override"] == sanitized_override


def test_gui_state_override_cannot_retarget_authorized_case(seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())

    override = runtime.tools.authorized_gui_state_override(
        {
            "db": db_session,
            "context": context,
            "scope": "case",
            "workspace_id": workspace.id,
            "case_id": case_a.id,
            "gui_state_override": {
                "workspace_id": "other-workspace",
                "case_id": case_b.id,
                "current_case_output_path": f"workspaces/{workspace.id}/cases/case-b",
                "layers": [{"id": "surface:lh:pial", "type": "surface"}],
            },
        }
    )

    assert override == {
        "layers": [{"id": "surface:lh:pial", "type": "surface"}],
        "workspace_id": workspace.id,
        "case_id": case_a.id,
    }


def test_run_chat_commits_preparation_before_model_loop(seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())

    class InspectingLoop:
        async def run(self, state):
            assert state["db"] is db_session
            assert not db_session.in_transaction()
            return {
                **state,
                "status": "completed",
                "final_response": "ready",
                "tool_calls_log": [],
                "reasoning_entries": [],
            }

    runtime.loop = InspectingLoop()  # type: ignore[assignment]

    payload = asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Reply with ready."}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            gui_session_id="gui-test",
            scope="case",
            provider=None,
            model=None,
            persist=True,
        )
    )

    assert payload["message"]["content"] == "ready"
    assert db_session.query(AssistantMessage).count() == 2


def test_system_prompt_exposes_sanitized_gui_state(tmp_path):
    write_prompt_config(tmp_path)
    prompt = build_system_prompt(
        tmp_path,
        {
            "scope": "case",
            "workspace_id": "workspace-1",
            "case_id": "case-1-id",
            "tool_specs": [],
            "gui_state": {
                "is_job_running": False,
                "workspace_id": "workspace-1",
                "case_id": "case-1-id",
                "layers": [
                    {
                        "id": "intensity:orig",
                        "filename": "orig.mgz",
                        "type": "intensity",
                        "role": "intensity",
                        "visible": True,
                        "opacity": 1.0,
                        "display": {},
                    },
                    {
                        "id": "segmentation:aseg",
                        "filename": "aparc.DKTatlas+aseg.deep.mgz",
                        "type": "segmentation",
                        "role": "segmentation",
                        "visible": False,
                        "opacity": 0.7,
                        "display": {},
                    },
                ],
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

    assert '"id": "intensity:orig"' in prompt
    assert '"type": "segmentation"' in prompt
    assert '"visible": false' in prompt
    assert '"current_intensity_volume": "orig.mgz"' in prompt
    assert '"current_cursor": {"voxel": [12.0, 34.25, 56.0], "label_id": 251, "label_name": "CC_Posterior"}' in prompt
    assert '"has_active_case": true' in prompt
    assert '"has_loaded_layers": true' in prompt
    assert '"workspace_id"' not in prompt
    assert "current_intensity_artifact_id" not in prompt


def test_workspace_system_prompt_says_case_mount_is_unavailable(tmp_path):
    write_prompt_config(tmp_path)
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
    assert "Catalog workflows use explicit /workspace paths" in prompt


def test_system_prompt_includes_complete_fragments_without_silent_truncation(tmp_path):
    information = "I" * 3000 + " END-OF-INFORMATION"
    write_prompt_config(tmp_path, information=information)

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

    assert "END-OF-INFORMATION" in prompt
    assert "<assistant_role>\nAssistant role\n</assistant_role>" in prompt
    assert "<system_information>" in prompt
    assert "<operating_rules>\nOperating rules\n</operating_rules>" in prompt


def test_prompt_fragments_fail_fast_when_missing_or_empty(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_text(tmp_path / "missing.md")
    empty = tmp_path / "empty.md"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prompt fragment is empty"):
        load_text(empty)


def test_system_prompt_marks_bounded_dynamic_collections(tmp_path):
    write_prompt_config(tmp_path)
    layers = [{"id": f"layer-{index}", "filename": f"layer-{index}.mgz"} for index in range(51)]
    cases = [{"case_id": f"case-{index}"} for index in range(51)]

    case_prompt = build_system_prompt(
        tmp_path,
        {
            "scope": "case",
            "workspace_id": "workspace-1",
            "case_id": "case-1",
            "tool_specs": [],
            "gui_state": {"case_id": "case-1", "layers": layers},
        },
    )
    workspace_prompt = build_system_prompt(
        tmp_path,
        {
            "scope": "workspace",
            "workspace_id": "workspace-1",
            "case_id": None,
            "tool_specs": [],
            "gui_state": {},
            "workspace_cases": cases,
        },
    )

    assert '"layer_count": 51' in case_prompt
    assert '"layers_omitted": 1' in case_prompt
    assert '"case_count":51' in workspace_prompt
    assert '"cases_omitted":1' in workspace_prompt


def test_catalog_tool_search_returns_configured_tool_payload():
    runtime = AssistantRuntime(FakeGuiRuntime())

    payload = json.loads(runtime.tools.catalog_tools.search(
        {}, ToolExecutionContext("search-1"), {"query": "fastsurfer segmentation", "top_k": 1}
    ).content)

    assert payload == [
        {
            "tool_id": "fastsurfer_segmentation",
            "label": "FastSurfer — Segmentation",
            "description": "Run FastSurfer segmentation without cortical surface reconstruction.",
            "score": 1.0,
        }
    ]


def test_catalog_tool_call_waits_for_synchronous_workflow(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    captured = {}
    submission_count = 0
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
        "gui_session_id": "gui-test",
    }
    bind = runtime.tools.catalog_executor.catalog_runtime_binds(state)[0]
    (Path(bind.host_path) / "input.mgz").write_bytes(b"volume")

    def fake_submit(**kwargs):
        nonlocal submission_count
        submission_count += 1
        captured.update(kwargs)
        kwargs["run"].status = RunStatus.completed
        kwargs["run"].result_json = {"status": "completed", "stdout": "1.0 1.0 1.0\n"}
        db_session.commit()
        return kwargs["job_id"]

    from api_service.assistant.tools import catalog_execution as catalog_execution_module

    monkeypatch.setattr(catalog_execution_module, "require_network_disabled_image", lambda image: image)
    monkeypatch.setattr(catalog_execution_module, "submit_neuroimaging_workflow", fake_submit)

    payload = json.loads(
        asyncio.run(
            runtime.tools.catalog_tools.call(
                state,
                ToolExecutionContext("call-1", external_run_id="stable-assistant-run"),
                {
                    "tool_id": "mri_info_resolution",
                    "inputs": ["/case/input.mgz"],
                },
            )
        ).content
    )

    assert payload["status"] == "completed"
    assert payload["tool_id"] == "mri_info_resolution"
    assert payload["result"]["stdout"] == "1.0 1.0 1.0\n"
    assert captured["run"].case_id == case_a.id
    assert db_session.get(Run, payload["run_id"]).status is RunStatus.completed

    replayed = json.loads(
        asyncio.run(
            runtime.tools.catalog_tools.call(
                state,
                ToolExecutionContext("call-1", external_run_id="stable-assistant-run"),
                {
                    "tool_id": "mri_info_resolution",
                    "inputs": ["/case/input.mgz"],
                },
            )
        ).content
    )
    assert replayed["run_id"] == payload["run_id"] == "stable-assistant-run"
    assert db_session.query(Run).filter(Run.id == "stable-assistant-run").count() == 1
    assert submission_count == 1


def test_catalog_run_wait_timeout_returns_none(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    run = Run(
        id="waiting-run",
        case_id=case_a.id,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        status=RunStatus.queued,
        run_type="mri_info",
    )
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(runtime.tools.catalog_executor.settings, "assistant_workflow_wait_seconds", 0.001)

    result = asyncio.run(
        runtime.tools.catalog_executor.wait_for_terminal_run(
            db_session,
            run_id=run.id,
            workspace_id=workspace.id,
            case_id=case_a.id,
        )
    )

    assert result is None


def test_catalog_run_list_defaults_to_ten_and_honors_case_scope(seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    for index in range(12):
        db_session.add(
            Run(
                id=f"case-a-run-{index:02d}",
                case_id=case_a.id,
                workspace_id=workspace.id,
                created_by_user_id=context.user.id,
                status=RunStatus.completed,
                run_type="mri_info",
            )
        )
    db_session.add(
        Run(
            id="case-b-run",
            case_id=case_b.id,
            workspace_id=workspace.id,
            created_by_user_id=context.user.id,
            status=RunStatus.failed,
            run_type="fastsurfer_full",
        )
    )
    db_session.commit()
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
        "gui_session_id": "gui-test",
    }

    default_result = runtime.tools.catalog_tools.list_runs(
        state,
        ToolExecutionContext("list-default"),
        {},
    )
    limited_result = runtime.tools.catalog_tools.list_runs(
        state,
        ToolExecutionContext("list-three"),
        {"limit": 3},
    )

    default_runs = json.loads(default_result.content)
    limited_runs = json.loads(limited_result.content)
    assert len(default_runs) == 10
    assert all(run["case_id"] == case_a.id for run in default_runs)
    assert all(run["run_id"] != "case-b-run" for run in default_runs)
    assert [run["run_id"] for run in limited_runs] == [
        "case-a-run-11",
        "case-a-run-10",
        "case-a-run-09",
    ]
    assert limited_result.details["limit"] == 3


def test_catalog_run_list_uses_workspace_scope_without_case_filter(seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    for run_id, case_id in (("case-a-run", case_a.id), ("case-b-run", case_b.id), ("workspace-run", None)):
        db_session.add(
            Run(
                id=run_id,
                case_id=case_id,
                workspace_id=workspace.id,
                created_by_user_id=context.user.id,
                status=RunStatus.completed,
                run_type="mri_info",
            )
        )
    db_session.commit()
    state = {
        "db": db_session,
        "context": context,
        "scope": "workspace",
        "workspace_id": workspace.id,
        "case_id": None,
        "gui_session_id": "gui-test",
    }

    result = runtime.tools.catalog_tools.list_runs(
        state,
        ToolExecutionContext("list-workspace"),
        {"limit": 20},
    )

    assert {run["run_id"] for run in json.loads(result.content)} == {
        "case-a-run",
        "case-b-run",
        "workspace-run",
    }


def test_catalog_tool_call_reports_synchronous_workflow_failure_as_tool_error(
    monkeypatch, seeded_context
):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
        "gui_session_id": "gui-test",
    }
    bind = runtime.tools.catalog_executor.catalog_runtime_binds(state)[0]
    (Path(bind.host_path) / "input.mgz").write_bytes(b"volume")

    def fake_submit(**kwargs):
        kwargs["run"].status = RunStatus.failed
        kwargs["run"].error_message = "invalid command flag"
        kwargs["run"].result_json = {
            "status": "failed",
            "return_code": 1,
            "stderr": "invalid command flag",
        }
        db_session.commit()
        return kwargs["job_id"]

    from api_service.assistant.tools import catalog_execution as catalog_execution_module

    monkeypatch.setattr(catalog_execution_module, "require_network_disabled_image", lambda image: image)
    monkeypatch.setattr(catalog_execution_module, "submit_neuroimaging_workflow", fake_submit)

    result = asyncio.run(
        runtime.tools.catalog_tools.call(
            state,
            ToolExecutionContext("call-failed", external_run_id="failed-assistant-run"),
            {"tool_id": "mri_info_resolution", "inputs": ["/case/input.mgz"]},
        )
    )

    assert result.is_error is True
    assert result.details["status"] == "failed"
    assert result.details["error"] == "invalid command flag"


def test_catalog_cancel_persists_run_before_canceling_job(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    run = Run(
        id="run-to-cancel",
        case_id=case_a.id,
        workspace_id=workspace.id,
        created_by_user_id=context.user.id,
        status=RunStatus.running,
        run_type="mri_info_resolution",
        job_id="job-to-cancel",
    )
    db_session.add(run)
    db_session.commit()
    canceled_jobs = []

    def cancel_job(job_id):
        db_session.expire_all()
        assert db_session.get(Run, run.id).status is RunStatus.canceled
        canceled_jobs.append(job_id)
        return True

    from api_service.jobs import job_manager

    monkeypatch.setattr(job_manager, "cancel", cancel_job)

    payload = json.loads(
        runtime.tools.catalog_executor.cancel_run(
            db_session,
            run_id=run.id,
            workspace_id=workspace.id,
            case_id=case_a.id,
        ).content
    )

    assert payload == {"run_id": run.id, "status": "canceled"}
    assert canceled_jobs == ["job-to-cancel"]


def test_run_analysis_tools_come_from_workflow_catalog():
    load_workflow_catalog.cache_clear()

    payload = run_analysis_workflows_payload()
    assert [tool["id"] for tool in payload] == [
        "mri_info",
        "mri_info_resolution",
        "fsqc",
        "fastsurfer_full",
        "fastsurfer_segmentation",
        "fastsurfer_fast",
    ]
    assert {tool["execution"]["mode"] for tool in payload} == {"synchronous", "background"}
    fastsurfer = next(tool for tool in payload if tool["id"] == "fastsurfer_fast")
    assert fastsurfer["outputs"][0]["name"] == "whole_brain_segmentation"
    assert fastsurfer["outputs"][0]["path"] == "mri/aparc.DKTatlas+aseg.deep.mgz"


def test_case_catalog_tool_call_passes_case_to_worker(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    captured = {}
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
        "gui_session_id": "gui-test",
    }
    bind = runtime.tools.catalog_executor.catalog_runtime_binds(state)[0]
    input_path = Path(bind.host_path) / "input.mgz"
    input_path.write_bytes(b"volume")

    def fake_submit(**kwargs):
        captured.update(kwargs)
        kwargs["run"].status = RunStatus.completed
        kwargs["run"].result_json = {"status": "completed"}
        db_session.commit()
        return kwargs["job_id"]

    from api_service.assistant.tools import catalog_execution as catalog_execution_module

    monkeypatch.setattr(catalog_execution_module, "require_network_disabled_image", lambda image: image)
    monkeypatch.setattr(catalog_execution_module, "submit_neuroimaging_workflow", fake_submit)

    payload = json.loads(
        asyncio.run(
            runtime.tools.catalog_tools.call(
                state,
                ToolExecutionContext("call-2", external_run_id="case-run-1"),
                {
                    "tool_id": "mri_info",
                    "inputs": ["/case/input.mgz"],
                },
            )
        ).content
    )

    assert payload["status"] == "completed"
    assert captured["run"].case_id == case_a.id


def test_catalog_tool_call_rejects_wrong_container_for_tool():
    runtime = AssistantRuntime(FakeGuiRuntime())

    result = runtime.tools.catalog_executor.catalog_tool_call(
        {
            "container_id": "bash_image",
            "tool_id": "fastsurfer_full",
            "tool_args": ["--help"],
        }
    )

    assert "Extra inputs are not permitted" in result.content


def test_catalog_tool_call_rejects_unknown_tool():
    runtime = AssistantRuntime(FakeGuiRuntime())

    result = runtime.tools.catalog_executor.catalog_tool_call(
        {"tool_id": "unsafe_tool", "inputs": []}
    )

    assert "Workflow 'unsafe_tool' was not found" in result.content


def test_catalog_tool_call_rejects_unprepared_image_before_creating_run(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    state = {
        "db": db_session,
        "context": context,
        "scope": "case",
        "workspace_id": workspace.id,
        "case_id": case_a.id,
    }
    bind = runtime.tools.catalog_executor.catalog_runtime_binds(state)[0]
    (Path(bind.host_path) / "input.mgz").write_bytes(b"volume")

    from api_service.assistant.tools import catalog_execution as catalog_execution_module

    def reject_image(_image):
        raise RuntimeError("Run `./scripts/run.sh prepare-tools`")

    monkeypatch.setattr(catalog_execution_module, "require_network_disabled_image", reject_image)
    result = runtime.tools.catalog_executor.catalog_tool_call(
        {"tool_id": "mri_info_resolution", "inputs": ["/case/input.mgz"]},
        [bind],
        db=db_session,
        user_id=context.user.id,
        workspace_id=workspace.id,
        case_id=case_a.id,
    )

    assert "prepare-tools" in result.content
    assert db_session.query(Run).count() == 0


def test_run_chat_handles_round_limit(monkeypatch):
    class ToolLoopModel:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            return AIMessage(
                content="",
                tool_calls=[{"id": "call-loop", "name": "read_stats", "args": {"case_id": "ext-case-1"}}],
            )

    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: ToolLoopModel())
    monkeypatch.setattr(assistant_runtime_module.settings, "assistant_max_rounds", 1)
    runtime = AssistantRuntime(FakeGuiRuntime())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "keep searching"}],
                workspace_id=None,
                case_id=None,
                gui_session_id="gui-test",
                scope="case",
                provider=None,
                model=None,
                persist=False,
            )
        )

    assert exc_info.value.status_code == 502
    assert "assistant/tool rounds" in exc_info.value.detail


def test_failed_turn_persists_completed_tool_evidence(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context

    class ToolLoopModel:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            return AIMessage(
                content="",
                tool_calls=[{"id": "call-loop", "name": "read_stats", "args": {"label_query": "BrainSegVol"}}],
            )

    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: ToolLoopModel())
    monkeypatch.setattr(assistant_runtime_module.settings, "assistant_max_rounds", 1)
    runtime = AssistantRuntime(FakeGuiRuntime())

    with pytest.raises(HTTPException):
        asyncio.run(runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Keep searching."}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            gui_session_id="gui-test",
            scope="case",
            provider=None,
            model=None,
            persist=True,
        ))

    rows = db_session.query(AssistantMessage).order_by(AssistantMessage.sequence).all()
    assert [row.role for row in rows] == ["user", "tool-calls", "assistant"]
    assert rows[1].metadata_json["toolCalls"][0]["name"] == "read_stats"
    assert str(rows[2].content_json["value"]).startswith("Assistant turn failed:")


def test_execute_tools_matches_tool_names_case_insensitively():
    runtime = AssistantRuntime(FakeGuiRuntime())

    async def execute(_context, arguments):
        return ToolResult.success(f"read {arguments['path']}")

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
    runtime = AssistantRuntime(FakeGuiRuntime())

    async def read_tool(_context, _arguments: dict[str, object]) -> ToolResult:
        return ToolResult.success("")

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
    runtime = AssistantRuntime(FakeGuiRuntime())

    async def execute(_context, _arguments):
        return ToolResult.error("Error: file not found on disk: /case/mri/missing.mgz")

    result = asyncio.run(
        runtime.loop._execute_tools(
            {
                "conversation": [],
                "tool_calls_log": [],
                "result": {},
                "round_count": 5,
                "diagnostic_request_id": "request-2",
                "pending_tool_calls": [{"name": "gui_load_layer", "arguments": {"file_path": "/case/mri/missing.mgz"}}],
                "tool_definitions": [
                    ToolDefinition(
                        name="gui_load_layer",
                        description="Load a layer.",
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


def test_gui_command_status_waits_for_browser_acknowledgement(monkeypatch):
    calls = 0

    async def execute(_context, _arguments):
        nonlocal calls
        calls += 1
        return ToolResult.success(json.dumps({"status": "pending" if calls < 3 else "acknowledged"}))

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    result = asyncio.run(AssistantRuntime(FakeGuiRuntime()).loop.tool_executor.execute_tool(
        ToolDefinition(
            name="gui_command_status",
            description="Check a browser command.",
            parameters={},
            execute=execute,
        ),
        ToolExecutionContext("gui-status-1"),
        {"command_id": "command-1"},
    ))

    assert json.loads(result.content)["status"] == "acknowledged"
    assert calls == 3


def test_run_chat_stops_identical_no_progress_tool_retry(monkeypatch):
    fake_model = FakeModel(
        [
            {
                "kind": "tool_calls",
                "tool_calls": [{"name": "read_stats", "arguments": {"label_query": "BrainSegVol"}}],
            },
            {
                "kind": "tool_calls",
                "tool_calls": [{"name": "read_stats", "arguments": {"label_query": "BrainSegVol"}}],
            },
        ]
    )
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    gui_runtime = FakeGuiRuntime()
    payload = asyncio.run(
        AssistantRuntime(gui_runtime).run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "Keep reading the same stats."}],
            workspace_id=None,
            case_id=None,
            gui_session_id="gui-test",
            scope="case",
            provider=None,
            model=None,
            persist=False,
        )
    )

    assert len(gui_runtime.tool_calls) == 1
    assert "identical arguments" in payload["message"]["content"]


def test_prompt_budget_compacts_newest_oversized_evidence(monkeypatch):
    runtime = AssistantRuntime(FakeGuiRuntime())
    monkeypatch.setattr(assistant_runtime_module.settings, "assistant_prompt_max_characters", 300)
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="HEAD" + ("x" * 1000) + "TAIL"),
        HumanMessage(content="schema"),
    ]

    bounded = runtime.loop._bounded_messages(messages)

    assert len(bounded) == 4
    assert "Context notice" in str(bounded[1].content)
    assert "HEAD" in str(bounded[2].content)
    assert "TAIL" in str(bounded[2].content)
    assert "omitted" in str(bounded[2].content)


def test_prompt_budget_rejects_limit_that_cannot_hold_required_context(monkeypatch):
    runtime = AssistantRuntime(FakeGuiRuntime())
    monkeypatch.setattr(assistant_runtime_module.settings, "assistant_prompt_max_characters", 100)
    messages = [
        SystemMessage(content="required system prompt"),
        HumanMessage(content="conversation" * 100),
        HumanMessage(content="required response contract"),
    ]

    with pytest.raises(ValueError, match="too small for the complete system prompt"):
        runtime.loop._bounded_messages(messages)


def test_model_messages_preserve_current_user_images():
    image = "data:image/png;base64,AAAA"
    messages = build_model_messages(
        "system",
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect this MRI"},
                {"type": "image_url", "image_url": {"url": image}},
            ],
        }],
    )

    content = messages[1].content
    assert isinstance(content, list)
    image_part = content[1]
    assert isinstance(image_part, dict)
    assert image_part["image_url"]["url"] == image


def test_execute_tools_finishes_turn_when_workflow_is_queued():
    runtime = AssistantRuntime(FakeGuiRuntime())

    async def execute(_context, _arguments):
        payload = {"tool_id": "mri_info_resolution", "run_id": "run-1", "status": "queued"}
        return ToolResult.success(json.dumps(payload), details=payload)

    result = asyncio.run(
        runtime.loop._execute_tools(
            {
                "conversation": [],
                "tool_calls_log": [],
                "result": {},
                "round_count": 1,
                "pending_tool_calls": [{"name": "tool_call", "arguments": {}}],
                "tool_definitions": [
                    ToolDefinition(
                        name="tool_call",
                        description="Queue a workflow.",
                        parameters={},
                        execute=execute,
                    )
                ],
            }
        )
    )

    assert result["status"] == "completed"
    assert result["pending_tool_calls"] == []
    assert "mri_info_resolution" in result["final_response"]
    assert "run-1" in result["final_response"]


def test_execute_tools_relays_tool_exceptions_to_model():
    runtime = AssistantRuntime(FakeGuiRuntime())
    events = []

    async def execute(_context, _arguments):
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
    assert result["conversation"][-1]["content"] == "read: Error: /case requires an active case"
    assert events == [("tool_call", result["tool_calls_log"][0])]


def test_execute_tools_requires_and_consumes_exact_confirmation():
    runtime = AssistantRuntime(FakeGuiRuntime())
    executions = []

    async def execute(_context, arguments):
        executions.append(arguments)
        return ToolResult.success("done")

    base_state = {
        "conversation": [],
        "tool_calls_log": [],
        "result": {},
        "round_count": 1,
        "pending_tool_calls": [{"name": "write", "arguments": {"path": "note.txt", "content": "hello"}}],
        "tool_definitions": [
            ToolDefinition(
                name="write",
                description="Write a file.",
                parameters={},
                execute=execute,
                risk=ToolRisk.write,
            )
        ],
    }

    waiting = asyncio.run(runtime.loop._execute_tools(base_state))
    assert waiting["status"] == "awaiting_approval"
    assert waiting["approval_request"]["name"] == "write"
    assert executions == []

    approved_state = {
        **base_state,
        "tool_approvals": [waiting["approval_request"]],
    }
    completed = asyncio.run(runtime.loop._execute_tools(approved_state))
    assert completed["status"] == "running"
    assert completed["tool_calls_log"][0]["result"] == "done"
    assert executions == [{"path": "note.txt", "content": "hello"}]


def test_workflow_confirmation_uses_catalog_presentation():
    runtime = AssistantRuntime(FakeGuiRuntime())

    async def execute(_context, _arguments):
        return ToolResult.success("unused")

    waiting = asyncio.run(runtime.loop._execute_tools({
        "conversation": [],
        "tool_calls_log": [],
        "result": {},
        "round_count": 1,
        "scope": "case",
        "pending_tool_calls": [{
            "name": "tool_call",
            "arguments": {
                "tool_id": "fastsurfer_segmentation",
                "inputs": ["/case/mri/001.mgz"],
            },
        }],
        "tool_definitions": [ToolDefinition(
            name="tool_call",
            description="Queue a workflow.",
            parameters={},
            execute=execute,
            risk=ToolRisk.workflow,
        )],
    }))

    approval = waiting["approval_request"]
    assert waiting["status"] == "awaiting_approval"
    assert approval["description"] == "run FastSurfer — Segmentation"
    assert approval["presentation"]["description"] == (
        "Run FastSurfer segmentation without cortical surface reconstruction."
    )
    assert approval["presentation"]["inputs"][0]["path"] == "/case/mri/001.mgz"
    assert approval["presentation"]["execution"] == {"mode": "background", "gpu": True}


def test_run_chat_persists_history(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    fake_model = FakeModel([{"kind": "final", "reasoning": "No tools needed.", "content": "Ready."}])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeGuiRuntime())

    asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Hello assistant"}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            gui_session_id="gui-test",
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
    assert db_session.query(AssistantTurn).one().status == "completed"
    thread_key = asyncio.run(runtime.get_thread_key(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))
    assert thread_key is not None and thread_key.startswith("private:")


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
    runtime = AssistantRuntime(FakeGuiRuntime())
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
            gui_session_id="gui-test",
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


def test_run_chat_continues_after_unknown_tool(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
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
    runtime = AssistantRuntime(FakeGuiRuntime())

    payload = asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Read the stats file"}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            gui_session_id="gui-test",
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
    history = asyncio.run(runtime.list_history(db_session, context, scope="case", workspace_id=workspace.id, case_id=case_a.id))
    assert [message.role for message in history] == ["user", "tool-calls", "assistant"]


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
    runtime = AssistantRuntime(FakeGuiRuntime())

    payload = asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Check the current case segmentation output"}],
            workspace_id=workspace.id,
            case_id=None,
            gui_session_id="gui-test",
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
    runtime = AssistantRuntime(FakeGuiRuntime())

    asyncio.run(
        runtime.run_chat(
            db=db_session,
            context=context,
            messages=[{"role": "user", "content": "Hello assistant"}],
            workspace_id=workspace.id,
            case_id=case_a.id,
            gui_session_id="gui-test",
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


def test_workspace_tools_are_workspace_specific(monkeypatch, seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    fake_model = FakeModel([])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeGuiRuntime())

    _definitions, tools = runtime.tools.build(
        {
            "db": db_session,
            "context": context,
            "scope": "workspace",
            "workspace_id": workspace.id,
        }
    )
    tool_names = [tool["function"]["name"] for tool in tools]

    assert "workspace_bash" not in tool_names
    assert "workspace_probe_bash" not in tool_names
    assert "workspace_case_file_tree" in tool_names
    assert "workspace_file_tree" in tool_names


def test_workspace_file_tools_do_not_advertise_case_mount(seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())

    _definitions, tools = runtime.tools.build(
        {
            "db": db_session,
            "context": context,
            "scope": "workspace",
            "workspace_id": workspace.id,
        }
    )
    read_tool = next(tool for tool in tools if tool["function"]["name"] == "read")
    description = read_tool["function"]["description"]
    path_description = read_tool["function"]["parameters"]["properties"]["path"]["description"]

    assert "/case is not available in workspace chat" in description
    assert "/case is not available in workspace chat" in path_description
    assert "active case mount at /case" not in description


def test_workspace_tools_do_not_expose_arbitrary_execution(seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())

    _definitions, tools = runtime.tools.build(
        {
            "db": db_session,
            "context": context,
            "scope": "workspace",
            "workspace_id": workspace.id,
        }
    )
    tool_names = {tool["function"]["name"] for tool in tools}

    assert "workspace_bash" not in tool_names
    assert "workspace_probe_bash" not in tool_names
    assert "workspace_case_file_tree" in tool_names
    assert "workspace_file_tree" in tool_names


def test_catalog_tools_are_local_pydantic_tools():
    runtime = AssistantRuntime(FakeGuiRuntime())

    tool_specs = [tool.as_openai_tool() for tool in runtime.tools.catalog_tools.build_tools({"scope": "case"})]
    tool_names = {tool["function"]["name"] for tool in tool_specs}

    assert tool_names == {
        "tool_search",
        "tool_inspect",
        "tool_call",
        "tool_run_list",
        "tool_run_status",
        "tool_run_cancel",
    }
    tool_call = next(tool for tool in tool_specs if tool["function"]["name"] == "tool_call")
    assert "execute" not in tool_call["function"]["parameters"]["properties"]
    run_list = next(tool for tool in tool_specs if tool["function"]["name"] == "tool_run_list")
    limit_schema = run_list["function"]["parameters"]["properties"]["limit"]
    assert limit_schema["default"] == 10
    assert limit_schema["maximum"] == 100


def test_image_discovery_is_one_compact_llm_tool():
    runtime = AssistantRuntime(FakeGuiRuntime())

    tool_specs = [tool.as_openai_tool() for tool in runtime.tools.image_tools.build_tools({})]

    assert [tool["function"]["name"] for tool in tool_specs] == ["tool_image_search"]
    schema = tool_specs[0]["function"]["parameters"]
    assert schema["properties"]["limit"]["maximum"] == 20
    assert schema["properties"]["latest_only"]["default"] is True


def test_case_scope_excludes_arbitrary_execution_tools(seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())

    _definitions, tool_specs = runtime.tools.build(
        {
            "db": db_session,
            "context": context,
            "scope": "case",
            "workspace_id": workspace.id,
            "case_id": case_a.id,
            "gui_session_id": "gui-test",
        }
    )
    tool_names = {tool["function"]["name"] for tool in tool_specs}

    assert "python_run" not in tool_names
    assert "bash" not in tool_names
    assert "read" in tool_names
    assert "write" in tool_names
    assert "edit" in tool_names
    assert "tool_search" in tool_names
    assert "tool_call" in tool_names
    assert "tool_probe" in tool_names
    assert {"tool_config_get", "tool_config_upsert", "tool_config_delete"}.issubset(tool_names)

    upsert_spec = next(
        tool for tool in tool_specs if tool["function"]["name"] == "tool_config_upsert"
    )
    schema = upsert_spec["function"]["parameters"]
    workflow_schema = schema["properties"]["definition"]
    assert workflow_schema["type"] == "object"
    assert "$ref" not in json.dumps(schema)
    script_description = workflow_schema["properties"]["script"]["description"]
    assert "${OUTPUTS[n]}" in script_description
    assert "${RUN_ID} is not available" in script_description
    output_schema = workflow_schema["properties"]["outputs"]["items"]
    assert "FreeSurfer-LUT" in output_schema["properties"]["metadata"]["description"]


def test_tool_probe_runs_without_mounts_in_isolated_container(monkeypatch):
    runtime = AssistantRuntime(FakeGuiRuntime())
    captured = {}

    monkeypatch.setattr(
        probe_tools_module,
        "resolve_or_prepare_image",
        lambda image, **_kwargs: captured.setdefault("prepared_image", image),
    )

    def fake_execute(request):
        captured["request"] = request
        return SimpleNamespace(returncode=0, stdout="DenoiseImage help\n", stderr="")

    monkeypatch.setattr(probe_tools_module, "execute_runtime_request", fake_execute)

    result = asyncio.run(
        runtime.tools.probe_tools.probe(
            {},
            ToolExecutionContext("probe-1"),
            {
                "image": "antsx/ants:v2.6.5",
                "script": "command -v DenoiseImage && DenoiseImage --help",
            },
        )
    )

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["return_code"] == 0
    assert payload["sandbox"]["workspace_mounted"] is False
    request = captured["request"]
    assert captured["prepared_image"] == "antsx/ants:v2.6.5"
    assert request.timeout_s == probe_tools_module.PROBE_TIMEOUT_SECONDS
    assert request.container_run.isolated is True
    assert request.container_run.binds == ()
    assert request.container_run.network_disabled is True
    assert request.container_run.gpu_enabled is False
    probe_script = request.container_run.command[-1]
    assert "ulimit -Hu 64" in probe_script
    assert "ulimit -Hv 524288" in probe_script
    assert probe_script.endswith("command -v DenoiseImage && DenoiseImage --help")


def test_tool_probe_rejects_latest_and_unprepared_images(monkeypatch):
    runtime = AssistantRuntime(FakeGuiRuntime())

    latest = asyncio.run(
        runtime.tools.probe_tools.probe(
            {},
            ToolExecutionContext("probe-latest"),
            {"image": "antsx/ants:latest", "script": "DenoiseImage --help"},
        )
    )
    assert latest.is_error is True
    assert "non-latest tag" in latest.content

    monkeypatch.setattr(
        probe_tools_module,
        "resolve_or_prepare_image",
        lambda _image, **_kwargs: (_ for _ in ()).throw(RuntimeError("image is not prepared locally")),
    )
    missing = asyncio.run(
        runtime.tools.probe_tools.probe(
            {},
            ToolExecutionContext("probe-missing"),
            {"image": "antsx/ants:v2.6.5", "script": "DenoiseImage --help"},
        )
    )
    assert missing.is_error is True
    assert "not prepared locally" in missing.content


def test_tool_probe_uses_resolved_first_use_image(monkeypatch):
    runtime = AssistantRuntime(FakeGuiRuntime())
    resolved = []
    monkeypatch.setattr(
        probe_tools_module,
        "resolve_or_prepare_image",
        lambda image, **_kwargs: resolved.append(image) or "/cache/ants.sif",
    )
    monkeypatch.setattr(
        probe_tools_module,
        "execute_runtime_request",
        lambda _request: SimpleNamespace(returncode=0, stdout="tool help\n", stderr=""),
    )

    result = asyncio.run(
        runtime.tools.probe_tools.probe(
            {},
            ToolExecutionContext("probe-first-use"),
            {"image": "ants_2.6.5:20260602", "script": "DenoiseImage --help"},
        )
    )

    assert result.is_error is False
    assert resolved == ["vnmd/ants_2.6.5:20260602"]


def test_catalog_config_get_returns_complete_effective_definition(monkeypatch, tmp_path, seeded_context):
    _db_session, context, _workspace, _case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())
    monkeypatch.setattr(runtime.tools.catalog_executor.settings, "fs_data_root", tmp_path / "data")

    payload = json.loads(
        runtime.tools.catalog_tools.config_get(
            {"context": context},
            ToolExecutionContext("config-get-1"),
            {"tool_id": "mri_info"},
        ).content
    )

    assert payload["source"] == "built_in"
    assert payload["definition"]["script"] == 'mri_info "${INPUTS[0]}"\n'


def test_catalog_tool_binds_active_case_without_output_alias(seeded_context):
    db_session, context, workspace, case_a, _case_b = seeded_context
    runtime = AssistantRuntime(FakeGuiRuntime())

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


def test_catalog_tool_binds_workspace_output_for_workspace_scope(seeded_context):
    db_session, context, workspace, _case_a, _case_b = seeded_context
    fs_root = assistant_runtime_module.settings.fs_data_root
    runtime = AssistantRuntime(FakeGuiRuntime())

    binds = runtime.tools.catalog_executor.catalog_runtime_binds(
        {
            "db": db_session,
            "context": context,
            "scope": "workspace",
            "workspace_id": workspace.id,
        }
    )
    assert [(bind.host_path, bind.container_path, bind.mode) for bind in binds] == [
        ((fs_root / "output" / "workspaces" / workspace.name).resolve(), "/workspace", "rw")
    ]


def test_tool_inspect_returns_image_but_hides_script():
    runtime = AssistantRuntime(FakeGuiRuntime())
    inspected = json.loads(runtime.tools.catalog_tools.inspect(
        {}, ToolExecutionContext("inspect-1"), {"tool_id": "mri_info"}
    ).content)

    assert inspected["image"] == "freesurfer_8.1.0:20260311"
    assert "script" not in inspected
    assert inspected["tool_id"] == "mri_info"


def test_workspace_list_cases_tool_uses_filesystem_names(monkeypatch, seeded_context):
    db_session, context, workspace, case_a, case_b = seeded_context
    fake_model = FakeModel([])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeGuiRuntime())
    case_a.title = "stale-database-name"
    db_session.commit()

    result = runtime.tools.workspace_tools.list_cases(
        {
            "db": db_session,
            "context": context,
            "workspace_id": workspace.id,
            "workspace_cases": [],
        },
        ToolExecutionContext("list-cases-1"),
    )
    parsed = json.loads(result.content)

    assert {entry["case_id"] for entry in parsed} == {case_a.id, case_b.id}
    assert {entry["title"] for entry in parsed} == {"case-a", "case-b"}
    assert all(entry["workspace_path"].startswith("/workspace/cases/") for entry in parsed)


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
    runtime = AssistantRuntime(FakeGuiRuntime())

    payload = asyncio.run(
        runtime.run_chat(
            db=None,
            context=None,
            messages=[{"role": "user", "content": "Use tools until you are done."}],
            workspace_id=None,
            case_id=None,
            gui_session_id="gui-test",
            scope="case",
            provider=None,
            model=None,
            persist=False,
        )
    )

    assert payload["message"]["content"] == "finished"
    assert len(payload["tool_calls_log"]) == 5


def test_run_chat_rejects_empty_native_response(monkeypatch):
    fake_model = FakeModel([{"kind": "final", "content": ""}])
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeGuiRuntime())

    with pytest.raises(HTTPException, match="neither content nor a tool call"):
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "Explain the failed tool call."}],
                workspace_id=None,
                case_id=None,
                gui_session_id="gui-test",
                scope="case",
                provider=None,
                model=None,
                persist=False,
            )
        )


def test_run_chat_requires_native_tool_binding(monkeypatch):
    class PlainTextModel:
        async def ainvoke(self, _messages):
            return AIMessage(content="ready")

    fake_model = PlainTextModel()
    monkeypatch.setattr(provider_module.provider_registry, "build_chat_model", lambda *args, **kwargs: fake_model)
    runtime = AssistantRuntime(FakeGuiRuntime())

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "Reply with the single word ready."}],
                workspace_id=None,
                case_id=None,
                gui_session_id="gui-test",
                scope="workspace",
                provider=None,
                model=None,
                persist=False,
            )
        )

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "Configured model does not support native tool calling"


def test_run_chat_reports_unconfigured_provider_before_model_call(monkeypatch):
    unavailable = provider_module.ModelConfig(
        provider="openai-compatible",
        provider_family="openai_compatible",
        model="qwen",
        base_url="https://api.example.invalid",
        available=False,
        availability_reason="LLM_BACKEND_API_KEY is not configured",
    )
    monkeypatch.setattr(provider_module.provider_registry, "get", lambda *args, **kwargs: unavailable)
    runtime = AssistantRuntime(FakeGuiRuntime())

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            runtime.run_chat(
                db=None,
                context=None,
                messages=[{"role": "user", "content": "Hello"}],
                workspace_id=None,
                case_id=None,
                gui_session_id="gui-test",
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
    runtime = AssistantRuntime(FakeGuiRuntime())
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
                gui_session_id="gui-test",
                scope="case",
                provider=None,
                model=None,
                persist=True,
            )
        )

    assert "Workspace not found" in str(excinfo.value)
