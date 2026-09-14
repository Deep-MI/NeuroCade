"""MCP scope deletion must retain replay safety and roll back filesystem cleanup."""

import pytest
from api_service.cases.operations import purge_case_rows
from api_service.mcp_adapter import service
from api_service.routers.workspaces import delete_workspace
from api_service.schemas import WorkspaceDeleteRequest
from sqlalchemy import event
from test_mcp_adapter import database as database

from backend_common.admin_reset import purge_case, purge_workspace
from backend_common.case_storage import case_storage_dir, ensure_case_storage_layout, workspace_storage_dir
from backend_common.db import AssistantToolExecution, Case, McpClient, Workspace
from backend_common.settings import get_settings


def ledger(db, status="planned"):
    row = AssistantToolExecution(
        id="execution",
        source="mcp",
        client_id="c",
        user_id="u",
        workspace_id="w",
        case_id="case-w",
        call_id="key",
        tool_name="tool_call",
        arguments_digest="digest",
        arguments_json={"tool_id": "fastsurfer_fast", "inputs": []},
        risk="workflow",
        status=status,
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("status", ["planned", "denied", "succeeded"])
def test_case_delete_invalidates_mcp_scope_without_replay(database, status):
    with database() as db:
        ledger(db, status)
        case = db.get(Case, "case-w")
        purge_case_rows(db, case)
        db.commit()
        row = db.get(AssistantToolExecution, "execution")
        db.refresh(row)
        assert row.case_id is None
        assert row.status == "denied"
        assert row.result_json["details"]["deleted_case_id"] == "case-w"
        assert row.arguments_digest == "digest"
        # A canonical sample case can be recreated, but the old key stays consumed.
        recreated = Case(id="case-w", workspace_id="w", owner_user_id="u", title="Replacement")
        db.add(recreated)
        db.commit()
        assert db.query(AssistantToolExecution).filter_by(client_id="c", call_id="key", status="denied").count() == 1


@pytest.mark.asyncio
async def test_case_read_does_not_prevent_deletion(database):
    with database() as db:
        await service.invoke(db, "c", "get_case", {"case_id": "case-w", "arguments": {}})
        purge_case_rows(db, db.get(Case, "case-w"))
        db.commit()
        assert db.get(Case, "case-w") is None


@pytest.mark.parametrize("revoked", [False, True])
def test_workspace_delete_removes_pairings_and_history(database, revoked):
    with database() as db:
        context = service.state_for(db, "c")[1]["context"]
        ledger(db, "denied")
        db.get(McpClient, "c").revoked = revoked
        db.commit()
        assert delete_workspace("w", WorkspaceDeleteRequest(confirm_non_empty_delete=True), db, context)["deleted"] == "w"
        assert db.get(McpClient, "c") is None
        assert db.query(AssistantToolExecution).count() == 0


def test_admin_reset_cleans_pairings_after_commit(database):
    settings = get_settings()
    with database() as db:
        ledger(db, "denied")
        root = workspace_storage_dir(settings, "w")
        purge_workspace(db, settings, db.get(Workspace, "w"))
        db.commit()
        assert not root.exists()
        assert db.get(McpClient, "c") is None
        assert db.query(AssistantToolExecution).count() == 0


def test_admin_reset_restores_files_and_mcp_rows_on_failure(database):
    settings = get_settings()
    with database() as db:
        ledger(db, "denied")
        original = case_storage_dir(settings, "w", "case-w") / "keep.txt"
        original.write_text("must survive rollback")

        def reject_workspace_delete(session, _context, _instances):
            if any(isinstance(row, Workspace) for row in session.deleted):
                raise RuntimeError("forced database failure")

        event.listen(db, "before_flush", reject_workspace_delete)
        with pytest.raises(RuntimeError, match="forced database failure"):
            purge_workspace(db, settings, db.get(Workspace, "w"))
        db.rollback()
        assert original.read_text() == "must survive rollback"
        assert db.get(Case, "case-w") is not None
        assert db.get(McpClient, "c") is not None
        assert db.get(AssistantToolExecution, "execution").case_id == "case-w"


def test_admin_case_reset_rollback_restores_original_over_new_seed(database):
    settings = get_settings()
    with database() as db:
        workspace, case = db.get(Workspace, "w"), db.get(Case, "case-w")
        original = case_storage_dir(settings, "w", "case-w") / "keep.txt"
        original.write_text("original")
        purge_case(db, settings, case, workspace)
        replacement = Case(id="case-w", workspace_id="w", owner_user_id="u", title=case.title)
        db.add(replacement)
        db.flush()
        ensure_case_storage_layout(settings, replacement, workspace)
        original.write_text("new seed")
        db.rollback()
        assert original.read_text() == "original"


@pytest.mark.parametrize("operation", ["api_case", "api_workspace", "admin_case", "admin_workspace"])
def test_unresolved_writer_blocks_deletion_and_reset(database, operation, monkeypatch):
    from api_service.cases.operations import delete_case_for_user
    from fastapi import HTTPException

    from backend_common.db import Run, RunStatus

    settings = get_settings()
    with database() as db:
        context = service.state_for(db, "c")[1]["context"]
        case, workspace = db.get(Case, "case-w"), db.get(Workspace, "w")
        original = case_storage_dir(settings, "w", "case-w") / "keep.txt"
        original.write_text("writer owns this tree")
        db.add(
            Run(
                id="run",
                created_by_user_id="u",
                case_id=case.id,
                workspace_id="w",
                status=RunStatus.failed,
                run_type="fastsurfer_fast",
                result_json={"output_ownership": "unresolved"},
            )
        )
        db.commit()
        if operation == "api_case":
            from api_service.cases import operations

            def forbidden_stage(*args, **kwargs):
                pytest.fail("An owned output tree must never be staged")

            monkeypatch.setattr(operations, "stage_path_for_deletion", forbidden_stage)
        with pytest.raises((HTTPException, ValueError)):
            if operation == "api_case":
                delete_case_for_user(db, context, case_id=case.id)
            elif operation == "api_workspace":
                delete_workspace("w", WorkspaceDeleteRequest(confirm_non_empty_delete=True), db, context)
            elif operation == "admin_case":
                purge_case(db, settings, case, workspace)
            else:
                purge_workspace(db, settings, workspace)
        db.rollback()
        assert original.read_text() == "writer owns this tree"


@pytest.mark.parametrize("operation", ["api_case", "api_workspace", "admin_case", "admin_workspace"])
def test_stale_snapshot_cannot_stage_new_writer_outputs(database, operation, monkeypatch):
    from api_service.cases import operations
    from fastapi import HTTPException

    from backend_common import admin_reset
    from backend_common.db import Run, RunStatus
    from backend_common.mcp_lifecycle import ScopeDeletionConflict

    settings = get_settings()
    with database() as db:
        context = service.state_for(db, "c")[1]["context"]
        case, workspace = db.get(Case, "case-w"), db.get(Workspace, "w")
        with database() as writer:
            writer.add(
                Run(
                    id="new-writer",
                    case_id="case-w",
                    workspace_id="w",
                    created_by_user_id="u",
                    status=RunStatus.running,
                    run_type="fastsurfer_fast",
                )
            )
            writer.commit()

        def forbidden_stage(*args, **kwargs):
            pytest.fail("Stale deletion must not move live output files")

        monkeypatch.setattr(operations, "stage_path_for_deletion", forbidden_stage)
        monkeypatch.setattr(admin_reset, "stage_deletion_for_transaction", forbidden_stage)
        with pytest.raises((HTTPException, ScopeDeletionConflict)):
            if operation == "api_case":
                operations.delete_case_for_user(db, context, case_id=case.id)
            elif operation == "api_workspace":
                delete_workspace("w", WorkspaceDeleteRequest(confirm_non_empty_delete=True), db, context)
            elif operation == "admin_case":
                purge_case(db, settings, case, workspace)
            else:
                purge_workspace(db, settings, workspace)
        db.rollback()
        assert db.get(Case, "case-w") is not None


def test_deletion_reservation_preserves_pending_caller_changes(database):
    from backend_common.mcp_lifecycle import reserve_scope_deletion

    with database() as db:
        workspace = db.get(Workspace, "other")
        workspace.description = "pending caller change"
        reserve_scope_deletion(db)
        assert db.get(Workspace, "other").description == "pending caller change"
        db.commit()
    with database() as db:
        assert db.get(Workspace, "other").description == "pending caller change"
