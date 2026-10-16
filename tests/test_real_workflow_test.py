"""Real-command test boundaries, independent copies and retained diagnostics."""

import asyncio
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from api_service.assistant.tools import workflow_test_tools as module
from api_service.assistant.tools.definition import ToolExecutionContext
from neurocade_runtime_tools.bridge import _CaptureBuffer
from neurocade_runtime_tools.bridge_client import RuntimeStoppedTimeout
from neurocade_runtime_tools.docker_runtime import build_docker_argv
from neurocade_runtime_tools.execution import BridgeBind, RuntimeContainerRunRequest, validate_synthetic_fixture
from neurocade_runtime_tools.protocol import RuntimeImageSpec


def draft(script="true"):
    return dict(
        id="test",
        image="vnmd/fsl_6.0.7.23:20261013",
        description="test",
        details="test",
        inputs=[dict(name="input", description="input", kind="file")],
        case_output_folder="result",
        outputs=[dict(name="result", type="other", path="result.txt", description="result", required=True)],
        script=script,
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "original"
    source.mkdir()
    (source / "input.txt").write_text("original")
    tools = module.AssistantWorkflowTestTools(settings=SimpleNamespace(fs_data_root=tmp_path))
    identity = {"user_id": "user", "workspace_id": "workspace", "case_id": "case"}
    monkeypatch.setattr(tools, "identity", lambda state: (source, state.get("identity", identity)))
    return tools, source, identity


def test_defaults_and_advisory_only():
    assert module.WorkflowTestArgs(definition=draft()).timeout_s == 10
    assert module.WorkflowTestArgs(definition=draft(), timeout_s=120).timeout_s == 120
    spec = module.AssistantWorkflowTestTools(settings=None).build_tools({})[0]
    assert not spec.risk.requires_confirmation
    assert "60 seconds" in spec.description and "15 lines" in spec.description


@pytest.mark.parametrize("outcome", ["completed", "failed", "startup_timeout"])
def test_real_execution_copy_and_logs(setup, monkeypatch, outcome):
    tools, source, owner = setup

    async def execute(request):
        assert request.timeout_s == 10
        run = request.container_run
        assert run.workflow_test and run.isolated and run.network_disabled and not run.synthetic_fixture
        copied = Path(run.binds[0].host_path)
        assert copied != source
        (copied / "input.txt").write_text("changed copy")
        output = Path(run.binds[1].host_path)
        (output / "result.txt").write_text("new result")
        text = "\n".join(str(i) for i in range(40))
        Path(request.stdout_path).write_text(text)
        if outcome == "startup_timeout":
            raise RuntimeStoppedTimeout("timeout confirmed stopped", stdout=text, stderr="last error")
        return SimpleNamespace(returncode=0 if outcome == "completed" else 2, stdout=text, stderr="last error", writer_stopped=True)

    monkeypatch.setattr(module, "execute_runtime_request_async", execute)

    async def trial():
        result = await tools.test({}, ToolExecutionContext("test"), {"definition": draft(), "inputs": ["input.txt"]})
        assert result.details["status"] == outcome
        assert result.details["stdout_tail"].splitlines() == [str(i) for i in range(25, 40)]
        test_id = result.details["test_id"]
        logs = await tools.test({}, ToolExecutionContext("log"), {"mode": "logs", "test_id": test_id, "max_bytes": 50000})
        assert logs.details["content"].splitlines()[0] == "0"
        denied = await tools.test(
            {"identity": {**owner, "user_id": "other"}}, ToolExecutionContext("denied"), {"mode": "logs", "test_id": test_id}
        )
        assert denied.is_error
        assert not (Path(tools.settings.fs_data_root) / (".workflow-test-" + test_id) / "case with spaces").exists()

    asyncio.run(trial())
    assert (source / "input.txt").read_text() == "original"


@pytest.mark.parametrize("link", ["file", "directory"])
def test_copy_rejects_symlinks(tmp_path, link):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private")
    (source / "input").symlink_to(outside if link == "directory" else outside / "secret")
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(OSError):
        module.copy_inputs(source, target, [PurePosixPath("input")])
    assert not list(target.iterdir())


def test_hardlinked_source_is_copied_independently(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "input").write_text("original")
    target = tmp_path / "target"
    target.mkdir()
    module.copy_inputs(source, target, [PurePosixPath("input")])
    assert (source / "input").stat().st_ino != (target / "input").stat().st_ino
    (target / "input").write_text("changed")
    assert (source / "input").read_text() == "original"


def request(root):
    case = root / ".workflow-test-test/case with spaces"
    output = case / "result"
    output.mkdir(parents=True)
    return RuntimeContainerRunRequest(
        image=RuntimeImageSpec("vnmd/test:1"),
        command=["true"],
        isolated=True,
        workflow_test=True,
        network_disabled=True,
        scratch_paths=("/workflow_output",),
        binds=(
            BridgeBind(str(case.relative_to(root)), "/case with spaces", "rw"),
            BridgeBind(str(output.relative_to(root)), "/workflow_output/output", "rw"),
        ),
    )


@pytest.mark.parametrize("mutation", ["original", "network", "symlink", "target", "host", "scratch"])
def test_runtime_rejects_escape(tmp_path, mutation):
    r = request(tmp_path)
    if mutation == "original":
        (tmp_path / "original").mkdir()
        r = replace(r, binds=(replace(r.binds[0], source_relative="original"), r.binds[1]))
    elif mutation == "network":
        r = replace(r, network_disabled=False)
    elif mutation == "symlink":
        (tmp_path / "alias").symlink_to(tmp_path / ".workflow-test-test", target_is_directory=True)
        r = replace(r, binds=(replace(r.binds[0], source_relative="alias/case with spaces"), r.binds[1]))
    elif mutation == "target":
        r = replace(r, binds=(replace(r.binds[0], container_path="/"), r.binds[1]))
    elif mutation == "host":
        r = replace(r, binds=(replace(r.binds[0], host_path=tmp_path), r.binds[1]))
    else:
        r = replace(r, scratch_paths=("/host",))
    with pytest.raises(ValueError):
        validate_synthetic_fixture(r, tmp_path)


def test_runtime_profile_keeps_isolation_but_allows_mcr(tmp_path):
    argv = build_docker_argv(request(tmp_path), data_root=tmp_path, platform="linux/amd64")
    assert "--read-only" in argv and "--cap-drop" in argv and "none" in argv
    assert "/tmp:rw,nosuid,nodev,exec" in argv
    assert "8g" in argv and "--cpus" in argv


def test_bridge_caps_log_file_without_stopping_pipe_drain(tmp_path):
    with (tmp_path / "log").open("w") as handle:
        buffer = _CaptureBuffer(limit=100, log=handle)
        buffer.append("a" * (10 * 1024 * 1024 + 1))
        buffer.append("last line")
    assert (tmp_path / "log").stat().st_size < 10 * 1024 * 1024 + 100
    assert "truncated" in (tmp_path / "log").read_text()
    assert buffer.value().endswith("last line")


def test_polling_status_is_not_blocked_as_duplicate():
    from api_service.assistant.tool_execution_store import AssistantToolExecutionStore
    from api_service.assistant.tool_executor import AssistantToolExecutor
    from api_service.assistant.tools.definition import ToolDefinition, ToolResult

    async def status(*args):
        return ToolResult.structured({"status": "running"})

    executor = AssistantToolExecutor(AssistantToolExecutionStore())
    arguments = {"mode": "status", "test_id": "a" * 32}
    tool = ToolDefinition("tool_workflow_test", "", {}, status)
    result = asyncio.run(
        executor.execute(
            {
                "tool_definitions": [tool],
                "pending_tool_calls": [{"call_id": "poll", "name": tool.name, "arguments": arguments}],
                "last_tool_call_fingerprint": executor.fingerprint(tool.name, arguments),
            }
        )
    )
    assert not result.get("finalization_reason")
    assert result["tool_calls_log"][0]["details"]["status"] == "running"


def test_bridge_rejects_original_case_log_paths(tmp_path):
    from neurocade_runtime_tools.bridge import BridgeRuntime
    from neurocade_runtime_tools.protocol import PROTOCOL_VERSION

    r = request(tmp_path)
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": "test",
        "timeout_s": 120,
        "stdout_relative": "original/out.log",
        "stderr_relative": "original/err.log",
        "container": {
            "image": r.image.to_dict(),
            "command": ["true"],
            "isolated": True,
            "workflow_test": True,
            "network_disabled": True,
            "scratch_paths": ["/workflow_output"],
            "binds": [{"source_relative": b.source_relative, "container_path": b.container_path, "mode": b.mode} for b in r.binds],
        },
    }
    bridge = BridgeRuntime(backend="docker", data_root=tmp_path, image_dir=tmp_path / "images")
    with pytest.raises(ValueError, match="logs must stay"):
        bridge._validated_request(payload)
    payload.update(stdout_relative=".workflow-test-test/logs/stdout.log", stderr_relative=".workflow-test-test/logs/stderr.log")
    _, paths = bridge._validated_request(payload)
    assert paths.timeout == 120  # Advisory permission, no 60-second harness gate.


def test_stale_outputs_cannot_pass(setup, monkeypatch):
    tools, source, _ = setup
    (source / "result").mkdir()
    (source / "result/result.txt").write_text("stale")
    value = draft()
    value["inputs"][0]["kind"] = "directory"

    async def execute(request):
        assert not (Path(request.container_run.binds[0].host_path) / "result/result.txt").exists()
        return SimpleNamespace(returncode=0, stdout="", stderr="", writer_stopped=True)

    monkeypatch.setattr(module, "execute_runtime_request_async", execute)
    result = asyncio.run(tools.test({}, ToolExecutionContext("test"), {"definition": value, "inputs": ["."]}))
    assert result.is_error
    assert "Required outputs" in result.details["error"]
    assert (source / "result/result.txt").read_text() == "stale"


def test_no_output_folder_and_multiple_inputs(setup, monkeypatch):
    tools, source, _ = setup
    (source / "second.txt").write_text("second")
    value = draft()
    value.pop("case_output_folder")
    value["inputs"].append({"name": "second", "description": "second"})

    async def execute(request):
        assert len(request.container_run.binds) == 1
        case = Path(request.container_run.binds[0].host_path)
        assert (case / "input.txt").read_text() == "original"
        assert (case / "second.txt").read_text() == "second"
        (case / "result.txt").write_text("result")
        return SimpleNamespace(returncode=0, stdout="", stderr="", writer_stopped=True)

    monkeypatch.setattr(module, "execute_runtime_request_async", execute)
    result = asyncio.run(tools.test({}, ToolExecutionContext("test"), {"definition": value, "inputs": ["input.txt", "second.txt"]}))
    assert result.details["status"] == "completed"


def test_long_test_returns_id_and_keeps_running(setup, monkeypatch):
    tools, _, _ = setup
    original_wait = asyncio.wait

    async def short_wait(tasks, **kwargs):
        return await original_wait(tasks, timeout=0.01)

    async def scenario():
        release = asyncio.Event()

        async def execute(request):
            await release.wait()
            output = Path(request.container_run.binds[1].host_path)
            (output / "result.txt").write_text("finished")
            return SimpleNamespace(returncode=0, stdout="finished", stderr="", writer_stopped=True)

        monkeypatch.setattr(module, "execute_runtime_request_async", execute)
        monkeypatch.setattr(module.asyncio, "wait", short_wait)
        result = await tools.test({}, ToolExecutionContext("test"), {"definition": draft(), "inputs": ["input.txt"], "timeout_s": 120})
        assert result.details["status"] in {"preparing", "running"}
        test_id = result.details["test_id"]
        task = module._TASKS[test_id]
        assert not task.done()
        release.set()
        await task
        result = await tools.test({}, ToolExecutionContext("status"), {"mode": "status", "test_id": test_id})
        assert result.details["status"] == "completed"
        assert result.details["stdout_tail"] == "finished"

    asyncio.run(scenario())
