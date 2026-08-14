"""Prepare and execute fixed workflows from the neuroimaging catalog."""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from neurocade_runtime_tools.container_request import RuntimeBind, build_container_request
from neurocade_runtime_tools.execution import RuntimeExecutionPolicy, RuntimeExecutionRequest, execute_runtime_request
from neurocade_runtime_tools.runtime_backends import resolve_gpu_enabled
from sqlalchemy.orm import Session

from api_service.runtime import settings
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow, resolve_workflow, workflows
from api_service.runtime_tools.workflow_outputs import (
    OutputBaseline,
    OutputState,
    classify_output,
    index_workflow_outputs,
    snapshot_workflow_outputs,
    write_output_baseline,
)
from backend_common.db import Case


def warm_workflow_gpu_capabilities() -> dict[str, bool]:
    """Warm cached CUDA probes for configured GPU-capable Run Analysis images."""
    images = sorted(
        {
            tool.neurodesk_image
            for tool in workflows()
            if tool.execution.gpu
        }
    )
    return {image: resolve_gpu_enabled(True, image=image) for image in images}


@dataclass(frozen=True)
class PreparedWorkflow:
    """A validated workflow invocation with resolved container and host paths."""

    tool: NeuroimagingWorkflow
    run_id: str
    bind: RuntimeBind
    container_root: str
    host_root: Path
    container_inputs: tuple[str, ...]
    host_outputs: tuple[Path, ...]
    container_outputs: tuple[str, ...]
    container_run_dir: str
    host_run_dir: Path
    gpu_enabled: bool


def _path_under_container_root(path: str, container_root: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    root = PurePosixPath(container_root)
    if not candidate.is_absolute():
        raise ValueError(f"Input path must be absolute: {path}")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Input path must stay under {container_root}: {path}") from exc
    if not relative.parts or ".." in relative.parts:
        raise ValueError(f"Input path must name a file under {container_root}: {path}")
    return relative


def _host_path_for_container_path(path: str, bind: RuntimeBind) -> Path:
    relative = _path_under_container_root(path, bind.container_path)
    root = Path(bind.host_path).expanduser().resolve()
    candidate = (root / Path(*relative.parts)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Input path escapes the authorized root: {path}") from exc
    if not candidate.is_file():
        raise ValueError(f"Input path must be an existing regular file: {path}")
    return candidate


def prepare_workflow(
    tool_id: str,
    inputs: list[str],
    bind: RuntimeBind,
    *,
    workflow: NeuroimagingWorkflow | None = None,
    run_id: str | None = None,
    gpu_enabled: bool | None = None,
) -> PreparedWorkflow:
    """Resolve one workflow invocation and validate all model-supplied paths."""
    tool = workflow or resolve_workflow(tool_id)
    if tool.id != tool_id:
        raise ValueError(f"Workflow definition id {tool.id!r} does not match requested tool id {tool_id!r}.")
    if len(inputs) != len(tool.inputs):
        raise ValueError(f"Tool {tool.id!r} requires exactly {len(tool.inputs)} ordered input file(s); received {len(inputs)}.")

    container_root = bind.container_path.rstrip("/")
    host_root = Path(bind.host_path).expanduser().resolve()
    if not host_root.is_dir():
        raise ValueError(f"Authorized workflow root does not exist: {host_root}")
    for path in inputs:
        _host_path_for_container_path(path, bind)

    resolved_run_id = run_id or str(uuid4())
    host_run_dir = (host_root / ".runs" / resolved_run_id).resolve()
    host_run_dir.relative_to(host_root)
    container_run_dir = f"{container_root}/.runs/{resolved_run_id}"

    host_outputs: list[Path] = []
    container_outputs: list[str] = []
    for output in tool.outputs:
        relative_text = output.path.replace("{run_id}", resolved_run_id)
        relative = PurePosixPath(relative_text)
        host_output = (host_root / Path(*relative.parts)).resolve()
        host_output.relative_to(host_root)
        host_outputs.append(host_output)
        container_outputs.append(f"{container_root}/{relative.as_posix()}")

    return PreparedWorkflow(
        tool=tool,
        run_id=resolved_run_id,
        bind=RuntimeBind(host_root, container_root, "rw"),
        container_root=container_root,
        host_root=host_root,
        container_inputs=tuple(inputs),
        host_outputs=tuple(host_outputs),
        container_outputs=tuple(container_outputs),
        container_run_dir=container_run_dir,
        host_run_dir=host_run_dir,
        gpu_enabled=(
            resolve_gpu_enabled(tool.execution.gpu, image=tool.neurodesk_image)
            if gpu_enabled is None
            else gpu_enabled
        ),
    )


def _readonly_array(name: str, values: tuple[str, ...] | list[str]) -> str:
    rendered = " ".join(shlex.quote(value) for value in values)
    return f"readonly -a {name}=({rendered})"


def workflow_script(prepared: PreparedWorkflow) -> str:
    """Build the trusted Bash program with safely quoted readonly context."""
    output_directories = sorted(
        {str(PurePosixPath(path).parent) for path in prepared.container_outputs}
    )
    tool = prepared.tool
    prologue = [
        _readonly_array("INPUTS", prepared.container_inputs),
        _readonly_array("OUTPUTS", prepared.container_outputs),
        f"readonly RUN_DIR={shlex.quote(prepared.container_run_dir)}",
        f"readonly CASE_ROOT={shlex.quote(prepared.container_root)}",
        f"readonly DEVICE={'cuda' if prepared.gpu_enabled else 'cpu'}",
        'mkdir -p "${RUN_DIR}"',
    ]
    if output_directories:
        prologue.append(
            "mkdir -p -- " + " ".join(shlex.quote(path) for path in output_directories)
        )
    return "\n".join([*prologue, tool.script])


def _bounded_stream(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = f"\n...[truncated {len(value) - limit} characters]...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return f"{value[:head]}{marker}{value[-tail:] if tail else ''}"


def _output_records(
    prepared: PreparedWorkflow,
    baseline: OutputBaseline | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for declaration, path, container_path in zip(
        prepared.tool.outputs,
        prepared.host_outputs,
        prepared.container_outputs,
        strict=True,
    ):
        exists = path.is_file() and not path.is_symlink()
        state = classify_output(path, baseline.get(declaration.name) if baseline is not None else None)
        records.append(
            {
                "name": declaration.name,
                "type": declaration.type,
                "path": container_path,
                "exists": exists,
                "size_bytes": path.stat().st_size if exists else None,
                "required": declaration.required,
                "state": state,
            }
        )
    return records


def _read_log(path: Path | None, limit: int) -> str:
    if path is None or not path.is_file():
        return ""
    size = path.stat().st_size
    if size <= limit:
        return path.read_text(encoding="utf-8", errors="replace")
    marker = f"\n...[truncated log of {size} bytes]...\n"
    budget = max(0, limit - len(marker))
    head_size = budget // 2
    tail_size = budget - head_size
    with path.open("rb") as handle:
        head = handle.read(head_size)
        handle.seek(max(0, size - tail_size))
        tail = handle.read(tail_size)
    return f"{head.decode('utf-8', errors='replace')}{marker}{tail.decode('utf-8', errors='replace')}"


def _index_output_records(
    prepared: PreparedWorkflow,
    output_records: list[dict[str, Any]],
    artifact_case_id: str | None,
    db: Session | None,
) -> None:
    if db is None or artifact_case_id is None:
        return
    output_states: dict[str, OutputState] = {
        record["name"]: record["state"]
        for record in output_records
    }
    case = db.get(Case, artifact_case_id)
    if case is not None:
        index_workflow_outputs(
            db,
            settings,
            case=case,
            workflow=prepared.tool,
            run_id=prepared.run_id,
            output_states=output_states,
        )


def execute_prepared_workflow(
    prepared: PreparedWorkflow,
    *,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    artifact_case_id: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Execute a prepared workflow and return its bounded public result."""
    prepared.host_run_dir.mkdir(parents=True, exist_ok=True)
    baseline = snapshot_workflow_outputs(prepared.tool, prepared.host_outputs)
    write_output_baseline(prepared.host_run_dir, baseline)
    command = build_container_request(
        image=prepared.tool.neurodesk_image,
        command=["/bin/bash", "-euo", "pipefail", "-c", workflow_script(prepared)],
        binds=[prepared.bind],
        cwd=prepared.container_root,
        disable_network=True,
        gpu=prepared.gpu_enabled,
    )
    request = RuntimeExecutionRequest(
        argv=[],
        cwd=prepared.host_root,
        timeout_s=prepared.tool.execution.timeout_s,
        execution_mode="container",
        output_root=prepared.host_root,
        workdir_root=prepared.host_root,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        capture_output=stdout_path is None and stderr_path is None,
        runtime_policy=RuntimeExecutionPolicy(network_disabled=True, gpu_enabled=prepared.gpu_enabled),
        log_lines=[
            f"NeuroCade workflow: {prepared.tool.label}",
            f"Image: {prepared.tool.image}",
            f"Device: {'cuda' if prepared.gpu_enabled else 'cpu'}",
            f"Run ID: {prepared.run_id}",
            "",
        ],
        container_run=command,
    )
    policy = prepared.tool.return_policy
    try:
        result = execute_runtime_request(request)
        stdout = result.stdout if stdout_path is None else _read_log(stdout_path, policy.max_stream_chars)
        stderr = result.stderr if stderr_path is None else _read_log(stderr_path, policy.max_stream_chars)
        output_records = _output_records(prepared, baseline)
        missing = [record["name"] for record in output_records if record["required"] and not record["exists"]]
        return_code = result.returncode
        if return_code == 0 and missing:
            return_code = 1
            suffix = f"Required output file(s) were not created: {', '.join(missing)}"
            stderr = f"{stderr.rstrip()}\n{suffix}".lstrip()
    except Exception:
        _index_output_records(
            prepared,
            _output_records(prepared, baseline),
            artifact_case_id,
            db,
        )
        raise
    finally:
        shutil.rmtree(prepared.host_run_dir, ignore_errors=True)

    _index_output_records(prepared, output_records, artifact_case_id, db)

    include = set(policy.include)
    payload: dict[str, Any] = {
        "tool_id": prepared.tool.id,
        "run_id": prepared.run_id,
        "status": "completed" if return_code == 0 else "failed",
    }
    if "return_code" in include or return_code != 0:
        payload["return_code"] = return_code
    if "stdout" in include:
        payload["stdout"] = _bounded_stream(stdout, policy.max_stream_chars)
    if "stderr" in include or return_code != 0:
        payload["stderr"] = _bounded_stream(stderr, policy.max_stream_chars)
    if "outputs" in include:
        payload["outputs"] = output_records
    payload["execution"] = {
        "image": prepared.tool.image,
        "mode": prepared.tool.execution.mode,
        "gpu": prepared.gpu_enabled,
        "timeout_s": prepared.tool.execution.timeout_s,
    }
    return payload


def execute_workflow(
    tool_id: str,
    inputs: list[str],
    bind: RuntimeBind,
    *,
    workflow: NeuroimagingWorkflow | None = None,
    run_id: str | None = None,
    gpu_enabled: bool | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    artifact_case_id: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Prepare and execute a workflow in one call."""
    prepared = prepare_workflow(
        tool_id,
        inputs,
        bind,
        workflow=workflow,
        run_id=run_id,
        gpu_enabled=gpu_enabled,
    )
    return execute_prepared_workflow(
        prepared,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        artifact_case_id=artifact_case_id,
        db=db,
    )
