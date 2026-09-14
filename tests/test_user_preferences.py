"""Preferences persist per user and control assistant approval on the server."""
import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from api_service.assistant import turn_streaming
from api_service.deps import get_context, get_db
from api_service.routers.auth import router
from api_service.schemas import AssistantTurnRequest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend_common.db import Base, User


def test_preferences_persist_and_control_turns(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'preferences.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as db:
        db.add_all([User(id=uid, email=f'{uid}@example.com', full_name=uid) for uid in ('one', 'two')])
        db.commit()
    app = FastAPI()
    app.include_router(router)
    active_user = ['one']

    def context():
        with sessions() as db:
            user = db.get(User, active_user[0])
            assert user is not None
            return SimpleNamespace(
                user=SimpleNamespace(
                    id=user.id,
                    assistant_approval=user.assistant_approval,
                )
            )

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_context] = context
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        assert client.get('/api/app/preferences').json() == {'light_mode': False, 'assistant_approval': True}
        assert client.patch('/api/app/preferences', json={'light_mode': True}).json()['assistant_approval'] is True
        assert client.patch('/api/app/preferences', json={'assistant_approval': False}).json()['light_mode'] is True
        assert client.patch('/api/app/preferences', json={'user_id': 'two'}).status_code == 422
        assert client.patch('/api/app/preferences', json={'assistant_approval': 'false'}).status_code == 422
        active_user[0] = 'two'
        assert client.get('/api/app/preferences').json() == {'light_mode': False, 'assistant_approval': True}
        active_user[0] = 'one'
    # A fresh HTTP session reads saved values, without any browser storage.
    with TestClient(app) as client:
        assert client.get('/api/app/preferences').json() == {'light_mode': True, 'assistant_approval': False}
    runtime = SimpleNamespace(run_chat=AsyncMock(return_value={}))
    payload = AssistantTurnRequest.model_validate({'messages': [{'role': 'user', 'content': 'Hello'}], 'workspace_id': 'workspace', 'gui_session_id': 'test'})
    for uid, expected in [('one', False), ('two', True)]:
        active_user[0] = uid
        asyncio.run(turn_streaming._run_chat(runtime=cast(Any, runtime), payload=payload, context=cast(Any, context()), emit=None, request_id='test'))
        assert runtime.run_chat.call_args.kwargs['require_tool_approval'] is expected
    engine.dispose()
