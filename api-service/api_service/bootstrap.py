"""Provide API service bootstrap behavior for NeuroCade."""

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from backend_common.db import Base, User
from backend_common.settings import ROOT_DIR, get_settings
from backend_common.workspace_bootstrap import ensure_personal_workspace


settings = get_settings()


def bootstrap_database(engine) -> None:
    """Create transient SQLite tables or apply Alembic migrations."""
    dialect_name = engine.dialect.name
    database_name = getattr(engine.url, "database", None)
    if dialect_name == "sqlite" and database_name in {None, "", ":memory:"}:
        Base.metadata.create_all(bind=engine)
        return

    alembic_config = Config(str(ROOT_DIR / "config" / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT_DIR / "migrations"))
    alembic_config.set_main_option("prepend_sys_path", str(ROOT_DIR))
    alembic_config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_url)
    command.upgrade(alembic_config, "head")


def seed_demo_state(db: Session) -> None:
    """Create local demo identities without importing filesystem output."""
    if settings.clerk_jwks_url:
        return
    user = db.get(User, settings.local_auth_user_id)
    if user is None:
        user = User(
            id=settings.local_auth_user_id,
            external_auth_id=settings.local_auth_user_id,
            email=settings.local_auth_email,
            full_name=settings.local_auth_name,
        )
        db.add(user)
    db.flush()
    ensure_personal_workspace(db, user)

    db.commit()
