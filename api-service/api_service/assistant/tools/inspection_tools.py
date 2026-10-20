"""Shared case/artifact inspection and workflow preparation tools."""

from fastapi import HTTPException

from api_service.artifacts.service import authorize_artifact_for_user, resolve_artifact_file_for_user
from api_service.assistant.tools.definition import ToolDefinition, ToolResult
from api_service.assistant.tools.run_logs import run_logs_tool
from api_service.assistant.tools.workflow_inputs import prepare_analysis_inputs
from api_service.cases.files import resolve_case_file
from api_service.helpers import get_case_for_user
from api_service.runtime_tools.workflow_catalog import resolve_workflow
from backend_common.db import Artifact
from backend_common.settings import get_settings


def schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


def additional_tools(state):
    items = [run_logs_tool(state)]

    async def prepare_analysis(_ctx, args):
        if not state.get("case_id"):
            raise HTTPException(400, "Select an explicit case_id before preparing analysis")
        case, _workspace, _role, _case_dir = get_case_for_user(
            state["db"], state["case_id"], state["context"].user.id, workspace_id=state["workspace_id"]
        )
        workflow = resolve_workflow(args["tool_id"], settings=get_settings(), user_id=state["context"].user.id)
        from api_service.schemas import WorkflowInputFile

        input_files = [WorkflowInputFile.model_validate(item) for item in args["input_files"]]
        if len(input_files) != len(workflow.inputs):
            raise HTTPException(400, f"Workflow requires exactly {len(workflow.inputs)} ordered inputs")
        arguments = {"tool_id": args["tool_id"], "input_files": [item.model_dump() for item in input_files]}
        prepare_analysis_inputs(state, arguments)
        return ToolResult.structured({"status": "inputs_valid", "case_id": case.id, "case_title": case.title,
            "workspace_id": state["workspace_id"], "preset": workflow.model_dump(mode="json", by_alias=True),
            "inputs": [item.model_dump() for item in input_files],
            "submission": {"case_id": case.id, "arguments": arguments},
            "next_step": "Show this case, input and preset to the user and confirm before tool_call. Follow its advertised submission schema. Runtime image readiness is checked at submission."})

    input_file_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": ["managed", "linked"]},
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        },
        "required": ["source", "path"],
        "additionalProperties": False,
    }
    items.append(ToolDefinition("prepare_analysis", "Validate selected case files or directories against an installed workflow preset and prepare an exact submission for user confirmation. Requires case_id. Does not start processing.", schema({"tool_id": {"type": "string"}, "input_files": {"type": "array", "items": input_file_schema, "minItems": 1, "maxItems": 20}}, ["tool_id", "input_files"]), prepare_analysis))

    async def list_artifacts(_ctx, args):
        query = state["db"].query(Artifact).filter(Artifact.workspace_id == state["workspace_id"])
        if state.get("case_id"):
            query = query.filter(Artifact.case_id == state["case_id"])
        if args.get("after"):
            query = query.filter(Artifact.id > args["after"])
        limit = args.get("limit", 20)
        rows = query.order_by(Artifact.id).limit(limit + 1).all()
        return ToolResult.structured(
            {
                "artifacts": [
                    {
                        "artifact_id": r.id,
                        "name": r.name,
                        "case_id": r.case_id,
                        "mime_type": r.mime_type,
                        "size_bytes": r.size_bytes,
                        "relative_path": r.relative_path,
                    }
                    for r in rows[:limit]
                ],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }
        )

    async def read_artifact(_ctx, args):
        from pathlib import Path

        artifact = state["db"].get(Artifact, args["artifact_id"])
        if (
            artifact is None
            or artifact.workspace_id != state["workspace_id"]
            or (state.get("case_id") and artifact.case_id != state["case_id"])
        ):
            raise HTTPException(404, "Artifact not found")
        metadata = artifact.metadata_json or {}
        case_id = artifact.case_id
        is_linked = metadata.get("source") == "linked" and isinstance(case_id, str)
        path_suffix = Path(artifact.relative_path).suffix.lower()
        if not (artifact.mime_type or "").startswith("text/") and path_suffix not in {
            ".txt",
            ".log",
            ".stats",
            ".csv",
            ".tsv",
            ".json",
        }:
            raise HTTPException(400, "Only bounded text artifacts are available; use NeuroCade for volumes and previews")
        offset = args.get("offset", 0)
        max_bytes = args.get("max_bytes", 20000)
        if is_linked:
            authorize_artifact_for_user(state["db"], state["context"], artifact.id)
            resolved = resolve_case_file(
                state["db"], state["context"], case_id, "linked", artifact.relative_path
            )
            data = b"".join(resolved.chunks(start=offset, end=offset + max_bytes - 1))
            size_bytes = resolved.size or 0
        else:
            _, path = resolve_artifact_file_for_user(state["db"], state["context"], artifact.id)
            with Path(path).open("rb") as stream:
                stream.seek(offset)
                data = stream.read(max_bytes)
            size_bytes = Path(path).stat().st_size
        return ToolResult.structured(
            {
                "artifact_id": artifact.id,
                "text": data.decode("utf-8", errors="replace"),
                "next_offset": offset + len(data),
                "size_bytes": size_bytes,
            }
        )

    paging = {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "after": {"type": "string", "maxLength": 255}}
    items += [
        ToolDefinition("list_artifacts", "List results and input artifacts in the selected scope.", schema(paging), list_artifacts),
        ToolDefinition(
            "read_artifact",
            "Read a bounded text artifact. Volumes are not sent to the agent.",
            schema(
                {
                    "artifact_id": {"type": "string", "maxLength": 255},
                    "offset": {"type": "integer", "minimum": 0},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 50000},
                },
                ["artifact_id"],
            ),
            read_artifact,
        ),
    ]
    return items
