"""current app schema baseline"""

from alembic import op
import sqlalchemy as sa


revision = "20260412000001"
down_revision = None
branch_labels = None
depends_on = None


role_enum = sa.Enum("owner", "user", "admin", name="roleenum")
artifact_kind_enum = sa.Enum("volume", "report", "log", "derived", name="artifactkind")
run_status_enum = sa.Enum("queued", "running", "completed", "failed", "canceled", name="runstatus")
assistant_scope_enum = sa.Enum("case", "workspace", name="assistantscope")


def upgrade() -> None:
    """Create the current application schema from a clean database."""
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("external_auth_id", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("external_auth_id"),
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspaces_owner_user_id", "workspaces", ["owner_user_id"])

    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("modalities_json", sa.JSON(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "title", name="uq_cases_workspace_title"),
    )
    op.create_index("ix_cases_owner_user_id", "cases", ["owner_user_id"])
    op.create_index("ix_cases_workspace_id", "cases", ["workspace_id"])

    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
    )
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    op.create_index("ix_workspace_memberships_workspace_id", "workspace_memberships", ["workspace_id"])

    op.create_table(
        "assistant_threads",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("thread_key", sa.String(length=255), nullable=False),
        sa.Column("scope_type", assistant_scope_enum, nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_key", name="uq_assistant_threads_thread_key"),
    )
    op.create_index("ix_assistant_threads_case_id", "assistant_threads", ["case_id"])
    op.create_index("ix_assistant_threads_created_by_user_id", "assistant_threads", ["created_by_user_id"])
    op.create_index("ix_assistant_threads_thread_key", "assistant_threads", ["thread_key"], unique=True)
    op.create_index("ix_assistant_threads_workspace_id", "assistant_threads", ["workspace_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("parent_run_id", sa.String(length=128), nullable=True),
        sa.Column("scope_type", assistant_scope_enum, nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=128), nullable=False),
        sa.Column("assistant_thread_id", sa.String(length=128), nullable=True),
        sa.Column("status", run_status_enum, nullable=False),
        sa.Column("run_type", sa.String(length=255), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("provider_name", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("runtime_job_id", sa.String(length=255), nullable=True),
        sa.Column("external_task_id", sa.String(length=255), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assistant_thread_id"], ["assistant_threads.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_assistant_thread_id", "runs", ["assistant_thread_id"])
    op.create_index("ix_runs_case_id", "runs", ["case_id"])
    op.create_index("ix_runs_case_status", "runs", ["case_id", "status"])
    op.create_index("ix_runs_created_by_user_id", "runs", ["created_by_user_id"])
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])
    op.create_index("ix_runs_parent_status", "runs", ["parent_run_id", "status"])
    op.create_index("ix_runs_runtime_job_id", "runs", ["runtime_job_id"])
    op.create_index("ix_runs_workspace_id", "runs", ["workspace_id"])
    op.create_index("ix_runs_workspace_scope_type", "runs", ["workspace_id", "scope_type"])
    op.create_index(
        "uq_runs_active_case",
        "runs",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("case_id IS NOT NULL AND status IN ('queued', 'running')"),
        sqlite_where=sa.text("case_id IS NOT NULL AND status IN ('queued', 'running')"),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("kind", artifact_kind_enum, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_case_id", "artifacts", ["case_id"])
    op.create_index("ix_artifacts_case_kind", "artifacts", ["case_id", "kind"])
    op.create_index(
        "uq_artifacts_case_relative_path",
        "artifacts",
        ["case_id", "relative_path"],
        unique=True,
        postgresql_where=sa.text("case_id IS NOT NULL"),
        sqlite_where=sa.text("case_id IS NOT NULL"),
    )
    op.create_index(
        "uq_artifacts_workspace_relative_path",
        "artifacts",
        ["workspace_id", "relative_path"],
        unique=True,
        postgresql_where=sa.text("case_id IS NULL AND workspace_id IS NOT NULL"),
        sqlite_where=sa.text("case_id IS NULL AND workspace_id IS NOT NULL"),
    )

    op.create_table(
        "case_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("artifact_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_case_events_artifact_id", "case_events", ["artifact_id"])
    op.create_index("ix_case_events_case_created", "case_events", ["case_id", "created_at"])
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
    op.create_index("ix_case_events_type_created", "case_events", ["event_type", "created_at"])
    op.create_index("ix_case_events_user_id", "case_events", ["user_id"])
    op.create_index("ix_case_events_workspace_created", "case_events", ["workspace_id", "created_at"])
    op.create_index("ix_case_events_workspace_id", "case_events", ["workspace_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("case_id", sa.String(length=255), nullable=True),
        sa.Column("artifact_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_case_created", "audit_events", ["case_id", "created_at"])
    op.create_index("ix_audit_events_case_id", "audit_events", ["case_id"])
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])

    op.create_table(
        "app_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_events_level_created", "app_events", ["level", "created_at"])
    op.create_index("ix_app_events_source_created", "app_events", ["source", "created_at"])
    op.create_index("ix_app_events_user_created", "app_events", ["user_id", "created_at"])
    op.create_index("ix_app_events_user_id", "app_events", ["user_id"])

    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["assistant_threads.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_assistant_messages_thread_sequence"),
    )
    op.create_index("ix_assistant_messages_case_id", "assistant_messages", ["case_id"])
    op.create_index("ix_assistant_messages_created_by_user_id", "assistant_messages", ["created_by_user_id"])
    op.create_index("ix_assistant_messages_thread_id", "assistant_messages", ["thread_id"])
    op.create_index("ix_assistant_messages_workspace_id", "assistant_messages", ["workspace_id"])

    op.create_table(
        "assistant_checkpoints",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("step_name", sa.String(length=255), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["assistant_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_checkpoints_thread_id", "assistant_checkpoints", ["thread_id"])
    op.create_index("ix_assistant_checkpoints_run_id", "assistant_checkpoints", ["run_id"])


def downgrade() -> None:
    """Downgrades are intentionally unsupported for the baseline reset."""
    raise NotImplementedError("This baseline migration does not support downgrade.")
