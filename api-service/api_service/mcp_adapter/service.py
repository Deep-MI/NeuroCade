"""Scoped external invocations using the built-in tools and execution ledger."""

import time
from uuid import uuid4

from fastapi import HTTPException
from jsonschema import validate
from sqlalchemy.exc import IntegrityError

from api_service.assistant.invocation import ToolInvocationService
from api_service.assistant.tool_execution_store import AssistantToolExecutionStore, arguments_digest, workflow_run_id
from api_service.assistant.tools.definition import ToolExecutionContext, ToolRisk
from api_service.assistant.tools.workflow_inputs import input_configuration, prepare_analysis_inputs
from api_service.helpers import get_case_for_user
from api_service.mcp_adapter.auth import resolve_client
from api_service.mcp_adapter.read_audit import ReadAuditStore, bounded_read_result
from api_service.monitoring.events import record_app_event_best_effort
from api_service.runtime_tools.workflow_catalog import resolve_workflow
from backend_common.db import AssistantToolExecution, run_with_sqlite_lock_retry
from backend_common.settings import get_settings

store = AssistantToolExecutionStore()



def state_for(db, client_id, case_id=None, gui_session_id=None):
    client, context = resolve_client(db, client_id)
    if case_id:
        get_case_for_user(db, case_id, context.user.id, workspace_id=client.workspace_id)
    if gui_session_id:
        from api_service.runtime.gui_runtime import gui_runtime

        sessions = gui_runtime.gui_state_store.sessions(user_id=context.user.id, workspace_id=client.workspace_id)
        if {"case_id": case_id, "gui_session_id": gui_session_id} not in sessions:
            raise HTTPException(404, "Viewer session unavailable for this user and case; refresh get_context")
    return client, {
        "db": db,
        "context": context,
        "workspace_id": client.workspace_id,
        "case_id": case_id,
        "scope": "case" if case_id else "workspace",
        "submit_without_wait": True,
        "gui_session_id": gui_session_id or "mcp-" + client.id,
    }


def schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


def definitions(state):
    return {tool.name: tool for tool in tool_builder().build(state)[0]}


def tool_builder():
    from api_service.assistant.tools.builder import AssistantToolBuilder
    from api_service.runtime.gui_runtime import gui_runtime

    return AssistantToolBuilder(gui_runtime, settings=get_settings())


def public_schema(tool):
    return schema(
        {
            "case_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "arguments": tool.parameters,
            "gui_session_id": {"type": "string", "minLength": 1, "maxLength": 255, "pattern": "^[^|]+$", "description": "Viewer session from get_context; required for viewer commands."},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 255},
        },
        ["arguments", "idempotency_key"] if tool.risk.requires_confirmation else ["arguments"],
    )




def serialize(row):
    if row.status == "planned" and row.expires_at and row.expires_at <= int(time.time()):
        status = "expired"
    else:
        status = "awaiting_approval" if row.status == "planned" else row.status
    return {
        "invocation_id": row.id,
        "client_id": row.client_id,
        "workspace_id": row.workspace_id,
        "case_id": row.case_id,
        "tool": row.tool_name,
        "arguments": row.arguments_json,
        "status": status,
        "run_id": row.external_run_id,
        "presentation": row.approval_json,
        "result": row.result_json,
        "expires_at": row.expires_at,
        "approval_url": "/local-agents?workspace=" + row.workspace_id + "&invocation=" + row.id,
    }


async def invoke(db, client_id, name, payload):
    # Planning includes a ledger insert. Acquire SQLite's write reservation before
    # its policy reads; execution and slow runtime preparation happen after commit.
    def begin_planning():
        db.rollback()
        db.connection(execution_options={"sqlite_begin_immediate": True})

    run_with_sqlite_lock_retry(db, begin_planning)
    client, state = state_for(db, client_id, payload.get("case_id"), payload.get("gui_session_id"))
    tool = definitions(state).get(name)
    if tool is None:
        raise HTTPException(404, "Tool unavailable")
    validate(payload, public_schema(tool))
    args = payload["arguments"]
    if tool.risk == ToolRisk.gui:
        if client.access != "standard" or get_settings().mcp_access != "standard":
            raise HTTPException(403, "Read-only connection cannot change the viewer")
        if name != "open_case" and not payload.get("gui_session_id"):
            raise HTTPException(400, "Select a gui_session_id from neurocade_get_context before changing the viewer")
    if not tool.risk.requires_confirmation:
        row = AssistantToolExecution(
            source="mcp",
            client_id=client_id,
            user_id=client.user_id,
            workspace_id=client.workspace_id,
            case_id=state["case_id"],
            call_id=str(uuid4()),
            tool_name=name,
            arguments_digest=arguments_digest(args),
            arguments_json=args,
            risk=tool.risk.value,
            status="approved",
        )
        db.add(row)
        db.commit()
        store.begin(db, row)
        result = await ToolInvocationService.execute_claimed(
            tool, ToolExecutionContext(call_id=row.call_id, execution_id=row.id), args, store=ReadAuditStore(), db=db, execution=row
        )
        db.refresh(row)
        return {**serialize(row), "result": bounded_read_result(result).as_dict()}
    if client.access != "standard" or get_settings().mcp_access != "standard":
        raise HTTPException(403, "Read-only connection")
    key = payload["idempotency_key"]
    digest = arguments_digest({"case_id": state["case_id"], "name": name, "arguments": args})
    existing = db.query(AssistantToolExecution).filter_by(client_id=client_id, call_id=key).one_or_none()
    if existing:
        if existing.arguments_digest != digest or existing.configuration_digest != input_configuration(state, tool, args):
            raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
        db.commit()
        return serialize(existing)
    config = input_configuration(state, tool, args)
    presentation = ToolInvocationService.approval_presentation(tool, args)
    row = AssistantToolExecution(
        source="mcp",
        client_id=client_id,
        workspace_id=client.workspace_id,
        user_id=client.user_id,
        case_id=state["case_id"],
        call_id=key,
        tool_name=name,
        arguments_digest=digest,
        arguments_json=args,
        risk=tool.risk.value,
        status="planned",
        configuration_digest=config,
        approval_json=presentation.model_dump(mode="json") if presentation else {},
        expires_at=int(time.time()) + get_settings().mcp_approval_seconds,
        external_run_id=workflow_run_id("mcp:" + client_id, key) if tool.creates_run else None,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return await invoke(db, client_id, name, payload)
    if not client.require_approval:
        return await decide(db, row, True)
    return serialize(row)


async def decide(db, row, approve):
    if row.status != "planned":
        return serialize(row)
    if not approve or (row.expires_at or 0) <= int(time.time()):
        db.query(AssistantToolExecution).filter_by(id=row.id, status="planned").update(
            {"status": "denied" if not approve else "expired"}, synchronize_session=False
        )
        db.commit()
        db.refresh(row)
        return serialize(row)
    client, state = state_for(db, row.client_id, row.case_id)
    if client.access != "standard" or get_settings().mcp_access != "standard":
        raise HTTPException(403, "Read-only connection")
    tool = definitions(state)[row.tool_name]
    if input_configuration(state, tool, row.arguments_json) != row.configuration_digest:
        db.query(AssistantToolExecution).filter_by(id=row.id, status="planned").update({"status": "expired"})
        db.commit()
        raise HTTPException(409, "Workflow or input changed; submit a new request for review")
    execution_context = ToolExecutionContext(call_id=row.call_id, execution_id=row.id, external_run_id=row.external_run_id)
    if tool.creates_run:
        workflow = resolve_workflow(row.arguments_json["tool_id"], settings=get_settings(), user_id=client.user_id)
        snapshot = workflow.model_dump_json(by_alias=True)
        expected_digest = row.configuration_digest
        client_id, case_id = client.id, row.case_id
        arguments = dict(row.arguments_json)

        def validate_submission(submitted_workflow):
            # Slow image validation has finished. Recheck at the synchronous
            # admission boundary and execute the exact snapshot checked here.
            db.expire_all()
            current_client, current_state = state_for(db, client_id, case_id)
            if current_client.access != "standard" or get_settings().mcp_access != "standard":
                raise HTTPException(403, "Read-only connection")
            prepared = prepare_analysis_inputs(current_state, arguments)
            if prepared.configuration_digest != expected_digest or arguments_digest(
                submitted_workflow.model_dump(mode="json", by_alias=True)
            ) != prepared.preset_digest:
                raise HTTPException(409, "Workflow or input changed; submit a new request for review")

        execution_context = ToolExecutionContext(
            call_id=row.call_id,
            execution_id=row.id,
            external_run_id=row.external_run_id,
            workflow_snapshot=snapshot,
            validate_submission=validate_submission,
        )
    replay = store.begin(db, row, approve_planned=True)
    if replay is None:
        result = await ToolInvocationService.execute_claimed(
            tool,
            execution_context,
            row.arguments_json,
            store=store,
            db=db,
            execution=row,
        )
        record_app_event_best_effort(
            db,
            source="mcp",
            level="error" if result.is_error else "info",
            event_type="mcp.invocation",
            message="External tool invocation completed",
            context=state["context"],
            details={"client_id": client.id, "invocation_id": row.id, "run_id": row.external_run_id, "tool": row.tool_name},
        )
    db.refresh(row)
    return serialize(row)
