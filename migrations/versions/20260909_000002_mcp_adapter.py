"""Allow external invocations without manufacturing assistant turns."""

import sqlalchemy as sa
from alembic import op

revision = "20260909000002"
down_revision = "20260909000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mcp_clients",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(128), sa.ForeignKey("workspaces.id", onupdate="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("access", sa.String(32), nullable=False),
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    with op.batch_alter_table("assistant_tool_executions") as batch:
        batch.alter_column("turn_id", existing_type=sa.String(128), nullable=True)
        batch.alter_column("thread_id", existing_type=sa.String(128), nullable=True)
        batch.add_column(sa.Column("source", sa.String(32), nullable=False, server_default="builtin_assistant"))
        batch.add_column(sa.Column("client_id", sa.String(128)))
        batch.add_column(sa.Column("configuration_digest", sa.String(64)))
        batch.add_column(sa.Column("approval_json", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("expires_at", sa.Integer()))
        batch.create_foreign_key("fk_execution_mcp_client", "mcp_clients", ["client_id"], ["id"])
        batch.create_unique_constraint("uq_tool_execution_client_call", ["client_id", "call_id"])
    op.create_table(
        "mcp_pairings",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("workspaces.id", onupdate="CASCADE", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("access", sa.String(32), nullable=False),
        sa.Column("require_approval", sa.Boolean(), nullable=False),
        sa.Column("code_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("installation_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    # Refuse destructive loss of external activity during downgrade.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM assistant_tool_executions WHERE client_id IS NOT NULL")).scalar():
        raise RuntimeError("Cannot downgrade while external invocation records exist")
    op.drop_table("mcp_pairings")
    with op.batch_alter_table("assistant_tool_executions") as batch:
        batch.drop_constraint("uq_tool_execution_client_call", type_="unique")
        batch.drop_constraint("fk_execution_mcp_client", type_="foreignkey")
        for name in ("source", "client_id", "configuration_digest", "approval_json", "expires_at"):
            batch.drop_column(name)
        batch.alter_column("turn_id", existing_type=sa.String(128), nullable=False)
        batch.alter_column("thread_id", existing_type=sa.String(128), nullable=False)
    op.drop_table("mcp_clients")
