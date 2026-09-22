"""Resolve and snapshot authorized workflow inputs at approval boundaries."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from api_service.cases.workflow_inputs import resolve_case_workflow_inputs
from api_service.helpers import get_case_for_user
from api_service.runtime_tools.workflow_catalog import NeuroimagingWorkflow, resolve_workflow
from api_service.runtime_tools.workflow_validation import validate_workflow_input
from api_service.schemas import WorkflowInputFile
from backend_common.db import AssistantScope
from backend_common.settings import get_settings


@dataclass(frozen=True)
class PreparedAnalysisInputs:
    workflow: NeuroimagingWorkflow
    container_inputs: tuple[str, ...]
    preset_digest: str
    configuration_digest: str
    linked_source_id: str | None = None
    linked_source_relative: str = "."


def _managed_path(state, relative_path: str, requirement) -> tuple[Path, str]:
    from api_service.assistant.tools.file_tools import AssistantFileTools

    path = AssistantFileTools(settings=get_settings()).resolve_path_sync(state, relative_path)
    validate_workflow_input(requirement, path, db=state["db"])
    container_root = "/case" if state["scope"] == AssistantScope.case.value else "/workspace"
    suffix = "" if relative_path == "." else f"/{relative_path}"
    return path, f"{container_root}{suffix}"


def prepare_analysis_inputs(state, arguments):
    """Resolve public case-relative references into authorized runtime paths."""
    from api_service.assistant.tool_execution_store import arguments_digest

    workflow = resolve_workflow(arguments["tool_id"], settings=get_settings(), user_id=state["context"].user.id)
    digest = arguments_digest(workflow.model_dump(mode="json", by_alias=True))
    supplied = arguments.get("input_files")
    if not isinstance(supplied, list) or len(supplied) != len(workflow.inputs):
        raise HTTPException(400, f"Workflow requires exactly {len(workflow.inputs)} ordered input reference(s)")
    try:
        input_files = [WorkflowInputFile.model_validate(value) for value in supplied]
    except ValueError as exc:
        raise HTTPException(400, "Workflow inputs must be authorized case-relative references") from exc

    container_inputs: list[str] = []
    linked_source_id: str | None = None
    linked_source_relative = "."
    if state["scope"] == AssistantScope.case.value:
        case_id = state.get("case_id")
        if not case_id:
            raise HTTPException(400, "Select a case before running a workflow")
        case, _workspace, _role, case_dir = get_case_for_user(
            state["db"], case_id, state["context"].user.id, workspace_id=state["workspace_id"]
        )
        resolved = resolve_case_workflow_inputs(state["db"], case, case_dir, input_files, workflow.inputs)
        container_inputs.extend(resolved.container_paths)
        linked_source_id = resolved.linked_source_id
        linked_source_relative = resolved.linked_source_relative
    else:
        for item, requirement in zip(input_files, workflow.inputs, strict=True):
            if item.source != "managed":
                raise HTTPException(400, "Linked inputs require an explicit case scope")
            _path, container_path = _managed_path(state, item.path, requirement)
            container_inputs.append(container_path)

    return PreparedAnalysisInputs(
        workflow,
        tuple(container_inputs),
        digest,
        arguments_digest({"workflow": digest, "input_files": [item.model_dump() for item in input_files]}),
        linked_source_id,
        linked_source_relative,
    )


def input_configuration(state, tool, arguments):
    return prepare_analysis_inputs(state, arguments).configuration_digest if tool.creates_run else None
