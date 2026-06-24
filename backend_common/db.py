"""Provide shared backend db utilities for NeuroCade."""

from collections.abc import Generator
from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Enum as SqlEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from backend_common.settings import get_settings


settings = get_settings()


def _build_engine(url: str) -> Engine:
    """Create the SQLAlchemy engine, tuned for SQLite/WAL single-node use.

    The API request threads and the in-process JobWorker write concurrently, so
    SQLite runs in WAL mode (concurrent readers + one serialized writer) with a
    busy timeout to ride out brief write contention. ``check_same_thread=False``
    lets a connection move between the worker and request threads.
    """
    if url.startswith("sqlite"):
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
        )
    return create_engine(url, future=True)


engine = _build_engine(settings.sqlalchemy_database_url)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):  # noqa: ANN001
    """Apply WAL/safety pragmas and hand transaction control to SQLAlchemy."""
    if engine.dialect.name != "sqlite":
        return
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        # Generous so a writer waits out another writer's short bookkeeping phase
        # instead of failing; long tool/container runs never hold a transaction.
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


@event.listens_for(engine, "begin")
def _sqlite_begin(conn):  # noqa: ANN001
    """Start SQLite transactions without taking the write lock for reads.

    Request handlers commonly perform read-only DB lookups before awaiting
    slower work. Using ``BEGIN IMMEDIATE`` for every transaction makes those
    reads hold SQLite's single write lock and can starve other API requests.
    Code that truly needs an eager write lock can opt in with the
    ``sqlite_begin_immediate`` execution option.
    """
    if engine.dialect.name != "sqlite":
        return
    if conn.get_execution_options().get("sqlite_begin_immediate"):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        return
    conn.exec_driver_sql("BEGIN")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RoleEnum(str, Enum):
    owner = "owner"
    user = "user"
    admin = "admin"


class ArtifactKind(str, Enum):
    volume = "volume"
    report = "report"
    log = "log"
    derived = "derived"


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class AssistantScope(str, Enum):
    case = "case"
    workspace = "workspace"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    external_auth_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)


class Case(Base, TimestampMixin):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "title", name="uq_cases_workspace_title"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    modalities_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="personal-workspace", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="personal", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class WorkspaceMembership(Base, TimestampMixin):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[RoleEnum] = mapped_column(SqlEnum(RoleEnum), nullable=False)
    granted_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_case_kind", "case_id", "kind"),
        Index(
            "uq_artifacts_case_relative_path",
            "case_id",
            "relative_path",
            unique=True,
            postgresql_where=text("case_id IS NOT NULL"),
            sqlite_where=text("case_id IS NOT NULL"),
        ),
        Index(
            "uq_artifacts_workspace_relative_path",
            "workspace_id",
            "relative_path",
            unique=True,
            postgresql_where=text("case_id IS NULL AND workspace_id IS NOT NULL"),
            sqlite_where=text("case_id IS NULL AND workspace_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    kind: Mapped[ArtifactKind] = mapped_column(SqlEnum(ArtifactKind), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class CaseEvent(Base):
    __tablename__ = "case_events"
    __table_args__ = (
        Index("ix_case_events_case_created", "case_id", "created_at"),
        Index("ix_case_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_case_events_type_created", "event_type", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_case_status", "case_id", "status"),
        Index("ix_runs_workspace_scope_type", "workspace_id", "scope_type"),
        Index("ix_runs_parent_status", "parent_run_id", "status"),
        Index(
            "uq_runs_active_case",
            "case_id",
            unique=True,
            postgresql_where=text("case_id IS NOT NULL AND status IN ('queued', 'running')"),
            sqlite_where=text("case_id IS NOT NULL AND status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    scope_type: Mapped[AssistantScope] = mapped_column(SqlEnum(AssistantScope), nullable=False, default=AssistantScope.case)
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assistant_thread_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_threads.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(SqlEnum(RunStatus), nullable=False, default=RunStatus.queued)
    run_type: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(255))
    provider_name: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(255))
    runtime_job_id: Mapped[str | None] = mapped_column(String(255), index=True)
    external_task_id: Mapped[str | None] = mapped_column(String(255))
    input_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_case_created", "case_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AppEvent(Base):
    __tablename__ = "app_events"
    __table_args__ = (
        Index("ix_app_events_level_created", "level", "created_at"),
        Index("ix_app_events_source_created", "source", "created_at"),
        Index("ix_app_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str | None] = mapped_column(String(16))
    path: Mapped[str | None] = mapped_column(String(1024))
    status_code: Mapped[int | None] = mapped_column(Integer)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AssistantThread(Base, TimestampMixin):
    __tablename__ = "assistant_threads"
    __table_args__ = (
        UniqueConstraint("thread_key", name="uq_assistant_threads_thread_key"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    thread_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    scope_type: Mapped[AssistantScope] = mapped_column(SqlEnum(AssistantScope), nullable=False, default=AssistantScope.case)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)


class AssistantMessage(Base, TimestampMixin):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_assistant_messages_thread_sequence"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(ForeignKey("assistant_threads.id"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AssistantCheckpoint(Base, TimestampMixin):
    __tablename__ = "assistant_checkpoints"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(ForeignKey("assistant_threads.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
