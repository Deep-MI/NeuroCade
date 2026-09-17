"""Persist user appearance and assistant approval preferences."""
import sqlalchemy as sa
from alembic import op

revision = "20260910000001"
down_revision = "20260909000002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("light_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("assistant_approval", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("users", "assistant_approval")
    op.drop_column("users", "light_mode")
