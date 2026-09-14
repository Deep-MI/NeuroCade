"""Every entry point respects PACS writers and bounded download reservations."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from api_service.cases.service import ensure_case_not_active
from api_service.runtime.run_admission import guard_output_submission
from fastapi import HTTPException
from test_mcp_adapter import database as database

from backend_common.admin_reset import purge_case
from backend_common.db import Case, PacsImport, Run, RunStatus, Workspace
from backend_common.output_activity import OutputBusy, ensure_outputs_idle, reserve_case_files
from backend_common.settings import get_settings
from backend_common.submission_lock import submission_lock


@pytest.mark.parametrize('state,code', [('queued', None), ('running', None), ('canceling', None), ('failed', 'cleanup_failed')])
def test_all_admission_and_reset_paths_block_pacs(database, state, code):
    with database() as db:
        db.add(PacsImport(id='import', workspace_id='w', case_id='case-w', user_id='u', source_id='source',
                         study_uid='1.2', submission_key='key', state=state, error_code=code))
        db.add(Run(id='next', workspace_id='w', case_id='case-w', created_by_user_id='u', status=RunStatus.queued, run_type='test'))
        db.commit()
        case = db.get(Case, 'case-w')
        with pytest.raises(HTTPException, match='PACS'):
            ensure_case_not_active(db, case)
        with pytest.raises(OutputBusy, match='PACS'):
            purge_case(db, get_settings(), case, db.get(Workspace, 'w'))
        db.rollback()
        @guard_output_submission
        def submit(run, workflow):
            pytest.fail('must not enqueue')
        with pytest.raises(ValueError, match='PACS'):
            submit(db.get(Run, 'next'), None)
        with pytest.raises(OutputBusy, match='PACS'):
            ensure_outputs_idle(db, 'w')
        ensure_outputs_idle(db, 'other')


def test_snapshot_releases_global_lock_and_cleans_up_on_failure(database):
    def unrelated_work():
        with submission_lock, database() as db:
            ensure_outputs_idle(db, 'other')
            return True

    def fail_snapshot(db):
        with reserve_case_files(db, 'w', 'case-w'):
            with ThreadPoolExecutor(1) as pool:
                assert pool.submit(unrelated_work).result(timeout=3)
            with pytest.raises(OutputBusy, match='download'):
                ensure_outputs_idle(db, 'w', 'case-w')
            with pytest.raises(OutputBusy, match='download'):
                ensure_outputs_idle(db, 'w')
            with pytest.raises(OutputBusy, match='download'):
                purge_case(db, get_settings(), db.get(Case, 'case-w'), db.get(Workspace, 'w'))
            db.rollback()
            raise RuntimeError('compression failed')

    with database() as db:
        with pytest.raises(RuntimeError, match='compression failed'):
            fail_snapshot(db)
        ensure_outputs_idle(db, 'w', 'case-w')
        with reserve_case_files(db, 'w', 'case-w'):
            pass
