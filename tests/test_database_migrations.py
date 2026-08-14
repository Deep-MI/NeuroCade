"""Regression tests for the clean database baseline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service import bootstrap as bootstrap_module  # noqa: E402

from backend_common.db import Base  # noqa: E402

MIGRATION_HEAD = "20260814000001"


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
            "unique_constraints": sorted(
                tuple(constraint["column_names"])
                for constraint in schema.get_unique_constraints(table_name)
            ),
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
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == MIGRATION_HEAD


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
