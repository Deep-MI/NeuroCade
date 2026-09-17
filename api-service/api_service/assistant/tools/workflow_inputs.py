"""Snapshot workflow presets and input identity before admission."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from api_service.assistant.tool_execution_store import arguments_digest
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow, resolve_workflow
from api_service.runtime_tools.workflow_validation import validate_workflow_inputs
from backend_common.settings import get_settings


@dataclass(frozen=True)
class PreparedAnalysisInputs:
    workflow: NeuroimagingWorkflow
    paths: tuple[Path, ...]
    preset_digest: str
    configuration_digest: str


def prepare_analysis_inputs(state, arguments):
    """Resolve and validate one preset once at the current approval boundary."""
    from api_service.assistant.tools.file_tools import AssistantFileTools

    workflow = resolve_workflow(arguments["tool_id"], settings=get_settings(), user_id=state["context"].user.id)
    digest = arguments_digest(workflow.model_dump(mode="json", by_alias=True))
    supplied = arguments.get("inputs")
    if not isinstance(supplied, list) or len(supplied) != len(workflow.inputs) or any(not isinstance(value, str) or not value for value in supplied):
        raise HTTPException(400, f"Workflow requires exactly {len(workflow.inputs)} nonempty ordered input path(s)")
    resolver = AssistantFileTools(settings=get_settings())
    inputs = []
    paths = []
    for value in supplied:
        resolved_value = value
        if state["scope"] == "workspace" and value.startswith("/workspace/"):
            resolved_value = value.removeprefix("/workspace/")
        path = resolver.resolve_path_sync(state, resolved_value)
        if not path.is_file():
            raise HTTPException(400, "Workflow input must be an existing file")
        paths.append(path)
        stat = path.stat()
        inputs.append({"input": value, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino})
    validate_workflow_inputs(workflow, paths, db=state["db"])
    return PreparedAnalysisInputs(workflow, tuple(paths), digest, arguments_digest({"workflow": digest, "inputs": inputs}))


def input_configuration(state, tool, arguments):
    return prepare_analysis_inputs(state, arguments).configuration_digest if tool.creates_run else None
