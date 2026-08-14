"""Provide shared backend db utilities for NeuroCade."""

import time
from collections.abc import Callable, Generator
from datetime import datetime
from enum import Enum
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event, func, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from backend_common.settings import get_settings

settings = get_settings()
T = TypeVar("T")

SQLITE_LOCK_RETRY_ATTEMPTS = 3
SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS = 0.05


def _build_engine(url: str) -> Engine:
    """Create the SQLAlchemy engine, tuned for SQLite/WAL single-node use.

    The API request threads and the in-process JobWorker write concurrently, so
    SQLite runs in WAL mode (concurrent readers + one serialized writer) with a
    busy timeout to ride out brief write contention. ``check_same_thread=False``
    lets a connection move between the worker and request threads.
    """
    if not url.startswith("sqlite"):
        raise RuntimeError("NeuroCade supports SQLite DATABASE_URL values only")
    return create_engine(
        url,
        future=True,
        connect_args={"check_same_thread": False},
    )


engine = _build_engine(settings.sqlalchemy_database_url)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):  # noqa: ANN001
    """Apply WAL/safety pragmas and hand transaction control to SQLAlchemy."""
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
    if conn.get_execution_options().get("sqlite_begin_immediate"):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        return
    conn.exec_driver_sql("BEGIN")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """Return whether an exception represents SQLite write-lock contention."""
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


def run_with_sqlite_lock_retry(
    db: Session,
    operation: Callable[[], T],
    *,
    attempts: int = SQLITE_LOCK_RETRY_ATTEMPTS,
    base_delay_seconds: float = SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS,
) -> T:
    """Run one complete DB unit of work with bounded SQLite-lock retries.

    The callback must contain the full transaction, including its commit.
    A failed attempt is rolled back before retrying so no partial ORM state is
    reused. Non-locking failures and exhausted retries are raised unchanged.
    """
    max_attempts = max(int(attempts), 1)
    for attempt in range(max_attempts):
        try:
            return operation()
        except OperationalError as exc:
            db.rollback()
            if not is_sqlite_lock_error(exc) or attempt + 1 >= max_attempts:
                raise
            time.sleep(max(float(base_delay_seconds), 0.0) * (2**attempt))
    raise RuntimeError("SQLite retry loop exhausted unexpectedly")


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
            sqlite_where=text("case_id IS NOT NULL"),
        ),
        Index(
            "uq_artifacts_workspace_relative_path",
            "workspace_id",
            "relative_path",
            unique=True,
            sqlite_where=text("case_id IS NULL AND workspace_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"))
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
        Index(
            "uq_runs_active_case",
            "case_id",
            unique=True,
            sqlite_where=text("case_id IS NOT NULL AND status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    scope_type: Mapped[AssistantScope] = mapped_column(SqlEnum(AssistantScope), nullable=False, default=AssistantScope.case)
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(SqlEnum(RunStatus), nullable=False, default=RunStatus.queued)
    run_type: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(128), index=True)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class BackgroundJob(Base, TimestampMixin):
    """Durable submission and lifecycle state for in-process background work."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        Index("ix_background_jobs_state_queue", "state", "queue_name"),
        Index("ix_background_jobs_finished_at", "finished_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    kwargs_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class AssistantTurn(Base, TimestampMixin):
    """Durable lifecycle record for one private assistant request."""

    __tablename__ = "assistant_turns"
    __table_args__ = (
        Index("ix_assistant_turns_thread_status", "thread_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    thread_id: Mapped[str] = mapped_column(ForeignKey("assistant_threads.id"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    request_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class AssistantToolExecution(Base, TimestampMixin):
    """Durable, exactly-addressed execution record for one assistant tool call."""

    __tablename__ = "assistant_tool_executions"
    __table_args__ = (
        UniqueConstraint("turn_id", "call_id", name="uq_assistant_tool_executions_turn_call"),
        Index("ix_assistant_tool_executions_turn_status", "turn_id", "status"),
        Index("ix_assistant_tool_executions_external_run", "external_run_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: str(uuid4()))
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_turns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[str] = mapped_column(ForeignKey("assistant_threads.id"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("cases.id", onupdate="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    risk: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    external_run_id: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
