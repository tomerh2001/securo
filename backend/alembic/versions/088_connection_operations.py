"""Persist account-update progress independently of browser navigation.

Revision ID: 088
Revises: 087
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "connection_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bank_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("message_code", sa.String(80), nullable=True),
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=name != "requested_at") for name in (
            "requested_at", "started_at", "finished_at", "source_last_success_before",
            "source_last_success_after", "imported_at", "collection_finished_before", "locked_until")],
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("refresh_dispatched", sa.Boolean(), nullable=False),
        sa.Column("lock_token", sa.String(36), nullable=True),
    )
    op.create_index("ix_connection_operations_connection_id", "connection_operations", ["connection_id"])
    op.create_index("ix_connection_operations_workspace_id", "connection_operations", ["workspace_id"])
    op.create_index("ux_connection_operation_active", "connection_operations", ["connection_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued','collecting','awaiting_verification','importing')"))


def downgrade():
    op.drop_table("connection_operations")
