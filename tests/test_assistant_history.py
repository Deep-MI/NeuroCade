"""Test assistant history behavior for NeuroCade."""

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant.conversation_store import persist_turn  # noqa: E402

from backend_common.auth import AuthContext  # noqa: E402
from backend_common.case_storage import build_case_id  # noqa: E402
from backend_common.db import AssistantMessage, AssistantScope, AssistantThread, Base, Case, RoleEnum, User, Workspace  # noqa: E402


def test_persist_turn_appends_conversation_jsonl(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    log_path = tmp_path / "assistant-conversations.jsonl"
    monkeypatch.setenv("NEUROCADE_ASSISTANT_CONVERSATION_LOG", str(log_path))

    try:
        user = User(id="user-1", external_auth_id="user-1", email="user@example.com", full_name="User")
        workspace = Workspace(
            id="workspace-1",
            owner_user_id=user.id,
            name="personal-workspace",
            kind="personal",
            is_default=True,
            status="active",
        )
        case = Case(id=build_case_id(workspace.id, "case-1"), workspace_id=workspace.id, owner_user_id=user.id, title="case-1")
        thread = AssistantThread(
            id="thread-1",
            thread_key=f"case:{case.id}",
            scope_type=AssistantScope.case,
            workspace_id=workspace.id,
            case_id=case.id,
            created_by_user_id=user.id,
            provider_name="provider",
            model_name="model",
        )
        db.add_all([user, workspace, case, thread])
        db.commit()

        persist_turn(
            db,
            AuthContext(user=user, role=RoleEnum.owner, auth_mode="local"),
            thread,
            incoming_messages=[{"role": "user", "content": "Please inspect the case"}],
            tool_calls_log=[{"name": "case_file_tree", "arguments": {}, "result": "/case/mri/orig.mgz"}],
            reasoning_entries=[{"round": 1, "summary": "Inspect files", "tool_names": ["case_file_tree"]}],
            assistant_content="I found orig.mgz.",
        )

        assert db.query(AssistantMessage).count() == 3
        records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 1
        assert records[0]["thread_key"] == f"case:{case.id}"
        assert records[0]["workspace_id"] == "workspace-1"
        assert [message["role"] for message in records[0]["messages"]] == ["user", "tool-calls", "assistant"]
        assert records[0]["messages"][0]["content_json"]["value"] == "Please inspect the case"
        assert records[0]["messages"][1]["metadata_json"]["toolCalls"][0]["name"] == "case_file_tree"
        assert records[0]["messages"][2]["content_json"]["value"] == "I found orig.mgz."
    finally:
        db.close()
