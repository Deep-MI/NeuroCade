"""Persist PACS imports separately from assistant-visible case metadata."""

import sqlalchemy as sa
from alembic import op

revision = "20260909000001"
down_revision = "20260814000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pacs_imports",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), sa.ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False),
        sa.Column("case_id", sa.String(255), sa.ForeignKey("cases.id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("study_uid", sa.String(64), nullable=False),
        sa.Column("submission_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(128)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("series_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "submission_key", name="uq_pacs_submission"),
    )
    for name in ("workspace_id", "case_id", "study_uid", "state"):
        op.create_index(f"ix_pacs_imports_{name}", "pacs_imports", [name])


def downgrade():
    op.drop_table("pacs_imports")
