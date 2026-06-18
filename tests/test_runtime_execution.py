"""Test shared runtime execution helpers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import cast

import pytest
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.cases import uploads as uploads_module  # noqa: E402
from api_service.runtime import execution as api_runtime_execution_module  # noqa: E402
from neurocade_runtime_tools.execution import (  # noqa: E402
    RuntimeArtifactIndexTarget,
    RuntimeCompletionHooks,
    RuntimeExecutionPolicy,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    execute_runtime_request,
)


def test_runtime_execution_captures_bounded_local_result(tmp_path):
    result = execute_runtime_request(
        RuntimeExecutionRequest(
            argv=[sys.executable, "-c", "print('ok')"],
            cwd=tmp_path,
            timeout_s=10,
            execution_mode="test-subprocess",
        )
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert result.execution_backend == "test-subprocess"


def test_runtime_execution_rejects_uncontained_log_path(tmp_path):
    with pytest.raises(ValueError, match="stdout log must stay under"):
        execute_runtime_request(
            RuntimeExecutionRequest(
                argv=[sys.executable, "-c", "print('nope')"],
                output_root=tmp_path / "allowed",
                stdout_path=tmp_path / "outside.log",
            )
        )


def test_dicom_conversion_uses_shared_runtime_execution(monkeypatch, tmp_path):
    input_dir = tmp_path / "dicom-input"
    output_dir = tmp_path / "dicom-output"
    input_dir.mkdir()
    output_dir.mkdir()
    captured: dict[str, RuntimeExecutionRequest] = {}

    monkeypatch.setattr(uploads_module.settings, "dicom_conversion_timeout_seconds", 17)
    monkeypatch.setattr(uploads_module.settings, "runtime_runner_url", "http://runtime-runner:58081")
    monkeypatch.setattr(uploads_module.settings, "runtime_runner_token", "secret")

    def fake_execute(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        captured["request"] = request
        return RuntimeExecutionResult(request=request, returncode=0, stdout="converted", stderr="")

    monkeypatch.setattr(uploads_module, "execute_runtime_request", fake_execute)

    uploads_module._run_dcm2niix(input_dir, output_dir)

    request = captured["request"]
    assert request.cwd == output_dir
    assert request.output_root == output_dir
    assert request.workdir_root == output_dir
    assert request.timeout_s == 17
    assert request.runtime_policy == RuntimeExecutionPolicy(network_disabled=True, gpu_enabled=False)
    assert request.execution_mode == "host-runtime-runner"
    assert request.runtime_runner_url == "http://runtime-runner:58081"
    assert request.runtime_runner_token == "secret"
    assert request.container_run is not None
    assert request.container_run.command[:2] == ["dcm2niix", "-z"]
    assert request.container_run.network_disabled is True
    assert request.container_run.gpu_enabled is False


def test_api_runtime_submission_uses_request_queue_metadata():
    captured: dict[str, object] = {}

    class FakeAsyncResult:
        id = "task-1"

    class FakeTask:
        def apply_async(self, **kwargs):
            captured["kwargs"] = kwargs
            return FakeAsyncResult()

    request = RuntimeExecutionRequest(
        argv=["api_service.example.task"],
        execution_mode="celery-submit",
        synchronous=False,
        queue_name="runtime-queue",
        user_id="user-1",
        workspace_id="workspace-1",
        case_id="workspace-1__case-1",
    )

    result = api_runtime_execution_module.submit_runtime_request(
        FakeTask(),
        request,
        kwargs={"case_id": "workspace-1__case-1"},
    )

    assert captured["kwargs"] == {
        "kwargs": {"case_id": "workspace-1__case-1"},
        "queue": "runtime-queue",
    }
    assert request.task_id == "task-1"
    assert result.submitted_task_id == "task-1"
    assert result.execution_backend == "celery-submit"


def test_api_runtime_submission_rejects_synchronous_request():
    with pytest.raises(ValueError, match="synchronous=False"):
        api_runtime_execution_module.submit_runtime_request(
            object(),
            RuntimeExecutionRequest(argv=["task"], synchronous=True),
        )


def test_runtime_completion_guard_runs_completion_once(monkeypatch):
    calls: list[object] = []
    commits: list[bool] = []

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def commit(self):
            commits.append(True)

    def fake_complete(db, request):
        calls.append((db, request))

    monkeypatch.setattr(api_runtime_execution_module, "complete_runtime_request", fake_complete)
    guard = api_runtime_execution_module.RuntimeCompletionGuard(lambda: FakeDb())
    request = RuntimeCompletionHooks(
        artifact_index_targets=(
            RuntimeArtifactIndexTarget(
                user_id="user-1",
                workspace_id="workspace-1",
                case_id="workspace-1__case-1",
            ),
        ),
    )

    assert guard.complete(request) is True
    assert guard.complete(request) is False
    assert len(calls) == 1
    assert commits == [True]


@pytest.mark.parametrize(
    ("fake_result", "expected_returncode", "expected_exception"),
    [
        (RuntimeExecutionResult(request=RuntimeExecutionRequest(argv=[]), returncode=0, stdout="ok", stderr=""), 0, None),
        (RuntimeExecutionResult(request=RuntimeExecutionRequest(argv=[]), returncode=7, stdout="", stderr="failed"), 7, None),
        (TimeoutError("Runtime command timed out after 1s"), None, TimeoutError),
    ],
)
def test_api_runtime_execution_runs_artifact_index_hooks_after_completion(monkeypatch, fake_result, expected_returncode, expected_exception):
    captured: dict[str, object] = {}
    db = cast(Session, object())

    def fake_execute(request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        if isinstance(fake_result, Exception):
            raise fake_result
        fake_result.request = request
        return fake_result

    def fake_index(db_arg, settings_arg, user_id, case_id, workspace_id, *, case_title=None, preferred_upload_name=None):
        captured["db"] = db_arg
        captured["user_id"] = user_id
        captured["case_id"] = case_id
        captured["workspace_id"] = workspace_id
        captured["case_title"] = case_title
        captured["preferred_upload_name"] = preferred_upload_name

    monkeypatch.setattr(api_runtime_execution_module, "_execute_runtime_request", fake_execute)
    monkeypatch.setattr(api_runtime_execution_module, "index_case_files_from_storage", fake_index)

    request = RuntimeExecutionRequest(
        argv=[sys.executable, "-c", "print('ok')"],
        timeout_s=1 if expected_exception is TimeoutError else None,
        artifact_index_targets=(
            RuntimeArtifactIndexTarget(
                user_id="user-1",
                workspace_id="workspace-1",
                case_id="workspace-1__case-1",
                case_title="case-1",
                preferred_upload_name="input.mgz",
            ),
        ),
    )

    if expected_exception is not None:
        with pytest.raises(expected_exception):
            api_runtime_execution_module.execute_runtime_request(request, db=db)
    else:
        result = api_runtime_execution_module.execute_runtime_request(request, db=db)
        assert result.returncode == expected_returncode

    assert captured == {
        "db": db,
        "user_id": "user-1",
        "case_id": "workspace-1__case-1",
        "workspace_id": "workspace-1",
        "case_title": "case-1",
        "preferred_upload_name": "input.mgz",
    }
