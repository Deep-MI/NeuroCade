"""Add host-linked case sources."""

import sqlalchemy as sa
from alembic import op

revision = "20260921000002"
down_revision = "20260910000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linked_source_claims",
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_index("ix_linked_source_claims_workspace_id", "linked_source_claims", ["workspace_id"])
    op.create_index("ix_linked_source_claims_owner_user_id", "linked_source_claims", ["owner_user_id"])
    op.create_table(
        "linked_case_sources",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_relative", sa.String(length=1024), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], onupdate="CASCADE", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["linked_source_claims.source_id"], onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], onupdate="CASCADE", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_linked_case_sources_case"),
    )
    op.create_index("ix_linked_case_sources_case_id", "linked_case_sources", ["case_id"])
    op.create_index("ix_linked_case_sources_workspace_id", "linked_case_sources", ["workspace_id"])
    op.create_index("ix_linked_case_sources_source_id", "linked_case_sources", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_linked_case_sources_source_id", table_name="linked_case_sources")
    op.drop_index("ix_linked_case_sources_workspace_id", table_name="linked_case_sources")
    op.drop_index("ix_linked_case_sources_case_id", table_name="linked_case_sources")
    op.drop_table("linked_case_sources")
    op.drop_index("ix_linked_source_claims_owner_user_id", table_name="linked_source_claims")
    op.drop_index("ix_linked_source_claims_workspace_id", table_name="linked_source_claims")
    op.drop_table("linked_source_claims")
