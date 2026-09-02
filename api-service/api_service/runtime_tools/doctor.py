"""Validate application storage, SQLite, and the required host runtime bridge."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from neurocade_runtime_tools.bridge_client import BridgeClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from backend_common.settings import get_settings


def main() -> int:
    settings = get_settings()
    failures = 0
    for path in (settings.fs_data_root, settings.outputs_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doctor-write-probe"
            probe.touch()
            probe.unlink()
            print(f"OK    {path} is writable")
        except OSError as exc:
            failures += 1
            print(f"FAIL  {path} is not writable: {exc}")
    try:
        health = BridgeClient.from_environment().health()
        if health.get("backend") != settings.neurocade_runtime:
            raise RuntimeError("application and bridge runtime profiles do not match")
        print(f"OK    runtime bridge protocol {health['protocol_version']} ({health['backend']})")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL  runtime bridge: {exc}")
    database_url = make_url(settings.sqlalchemy_database_url)
    if database_url.get_backend_name() == "sqlite" and database_url.database:
        engine = create_engine(settings.sqlalchemy_database_url)
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
        if revision:
            ScriptDirectory.from_config(Config(str(Path(__file__).parents[3] / "config" / "alembic.ini"))).get_revision(revision)
        print("OK    SQLite configuration is valid")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
