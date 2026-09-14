"""Regression tests for the clean database baseline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import bootstrap as bootstrap_module  # noqa: E402

from backend_common.db import Base  # noqa: E402


def _configure_database(monkeypatch, database_path: Path):
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setattr(bootstrap_module.settings, "database_url", database_url)
    return create_engine(database_url, future=True)


def _schema_signature(engine) -> dict[str, Any]:
    schema = inspect(engine)
    signature: dict[str, Any] = {}
    for table_name in sorted(set(schema.get_table_names()) - {"alembic_version"}):
        signature[table_name] = {
            "columns": {
                column["name"]: {
                    "type": str(column["type"]),
                    "nullable": column["nullable"],
                    "primary_key": column["primary_key"],
                }
                for column in schema.get_columns(table_name)
            },
            "foreign_keys": sorted(
                (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                    tuple(sorted(foreign_key.get("options", {}).items())),
                )
                for foreign_key in schema.get_foreign_keys(table_name)
            ),
            "indexes": sorted(
                (
                    index["name"],
                    tuple(index["column_names"]),
                    bool(index["unique"]),
                )
                for index in schema.get_indexes(table_name)
            ),
            "unique_constraints": sorted(tuple(constraint["column_names"]) for constraint in schema.get_unique_constraints(table_name)),
        }
    return signature


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "config" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


def test_migration_baseline_creates_current_schema(monkeypatch, tmp_path):
    engine = _configure_database(monkeypatch, tmp_path / "baseline.sqlite")

    bootstrap_module.bootstrap_database(engine)

    schema = inspect(engine)
    assert set(schema.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ScriptDirectory.from_config(_alembic_config(str(engine.url))).get_current_head()


def test_migration_baseline_matches_orm_metadata(monkeypatch, tmp_path):
    migration_engine = _configure_database(monkeypatch, tmp_path / "migration.sqlite")
    orm_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orm.sqlite'}", future=True)

    bootstrap_module.bootstrap_database(migration_engine)
    Base.metadata.create_all(bind=orm_engine)

    assert _schema_signature(migration_engine) == _schema_signature(orm_engine)


def test_migration_baseline_downgrades_to_empty_database(monkeypatch, tmp_path):
    engine = _configure_database(monkeypatch, tmp_path / "downgrade.sqlite")
    bootstrap_module.bootstrap_database(engine)

    command.downgrade(_alembic_config(str(engine.url)), "base")

    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}


def test_mcp_upgrade_preserves_existing_assistant_ledger(monkeypatch, tmp_path):
    engine = _configure_database(monkeypatch, tmp_path / "upgrade.sqlite")
    config = _alembic_config(str(engine.url))
    command.upgrade(config, "20260814000001")
    with engine.begin() as connection:
        statements = [
            "INSERT INTO users (id,email,full_name) VALUES ('u','u@test.invalid','Test')",
            "INSERT INTO workspaces (id,owner_user_id,name,kind,is_default) VALUES ('w','u','workspace','personal',1)",
            "INSERT INTO assistant_threads (id,thread_key,scope_type,workspace_id,provider_name,model_name) VALUES ('t','key','workspace','w','test','test')",
            "INSERT INTO assistant_turns (id,thread_id,workspace_id,user_id,status,request_json,result_json) VALUES ('turn','t','w','u','completed','{}','{}')",
            "INSERT INTO assistant_tool_executions (id,turn_id,thread_id,workspace_id,user_id,call_id,tool_name,arguments_digest,arguments_json,risk,status,result_json) VALUES ('e','turn','t','w','u','call','read','digest','{}','read','succeeded','{}')",
        ]
        for statement in statements:
            connection.execute(text(statement))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT turn_id, thread_id, status, source FROM assistant_tool_executions WHERE id='e'")).one()
        assert tuple(row) == ("turn", "t", "succeeded", "builtin_assistant")


def test_preferences_and_pairing_upgrade_preserves_existing_user_choices(monkeypatch, tmp_path):
    engine = _configure_database(monkeypatch, tmp_path / 'preferences-upgrade.sqlite')
    config = _alembic_config(str(engine.url))
    command.upgrade(config, '20260909000002')
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id,email,full_name) VALUES ('u','u@test.invalid','Test')"))
    command.upgrade(config, '20260910000001')
    with engine.begin() as connection:
        assert tuple(connection.execute(text("SELECT light_mode, assistant_approval FROM users WHERE id='u'")).one()) == (0, 1)
        connection.execute(text("UPDATE users SET light_mode=1, assistant_approval=0 WHERE id='u'"))
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        assert tuple(connection.execute(text("SELECT light_mode, assistant_approval FROM users WHERE id='u'")).one()) == (1, 0)
        assert connection.execute(text('SELECT COUNT(*) FROM mcp_pairings')).scalar_one() == 0
