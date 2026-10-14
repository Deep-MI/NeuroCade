"""Expose existing-file registration to embedded and external agents."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from api_service.artifacts.registration import RegisterCaseFile, register_case_file
from api_service.assistant.approval_contracts import AssistantActionApprovalPresentation
from api_service.assistant.tools.definition import ToolResult, ToolRisk
from api_service.assistant.tools.registration import ToolRegistration
from backend_common.db import run_with_sqlite_lock_retry


def registration_presentation(_state, arguments):
    request = RegisterCaseFile.model_validate(arguments)
    return AssistantActionApprovalPresentation.model_validate({
        'kind': 'action', 'action': 'file_register', 'title': 'Register existing case file?',
        'description': 'Make this file available in the load list, updating its layer metadata if already registered.',
        'confirm_label': 'Register file', 'tone': 'warning',
        'sections': [{'label': 'File registration', 'rows': [
            {'label': 'Path', 'value': request.file_path, 'code': True},
            {'label': 'Layer type', 'value': request.layer_type, 'code': False},
            {'label': 'LUT', 'value': request.lut or 'None', 'code': False},
        ]}],
    })


def artifact_tools(state):
    def execute(state, _execution, arguments):
        if not state.get('case_id'):
            raise HTTPException(400, 'Select a case before registering a file.')
        request = RegisterCaseFile.model_validate(arguments)

        # Fresh, short transaction: never upgrade an old assistant read snapshot.
        with Session(bind=state['db'].get_bind(), expire_on_commit=False) as db:
            def register():
                db.connection(execution_options={'sqlite_begin_immediate': True})
                result = register_case_file(db, state['context'], workspace_id=state['workspace_id'],
                                            case_id=state['case_id'], request=request)
                db.commit()
                return result

            return ToolResult.structured(run_with_sqlite_lock_retry(db, register))

    return [ToolRegistration(
        'register_case_file',
        'Register an existing managed /case/... volume or FreeSurfer surface so it appears in the load list. '
        'Supply layer_type explicitly and lut for segmentations. Also corrects existing layer metadata without '
        'changing file contents or run history. Idempotent for the same case/path. Does not run processing or '
        'load the viewer: after registration call gui_load_layer, then verify gui_command_status and gui_list_layers. '
        'Use this when a successful workflow produced an untracked file; do not rerun processing just to register it.',
        RegisterCaseFile.model_json_schema(), execute, risk=ToolRisk.write, approval_presentation=registration_presentation,
    ).bind(state)]
