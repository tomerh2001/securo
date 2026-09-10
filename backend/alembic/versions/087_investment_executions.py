"""Preserve source executions and archival valuation provenance.

Revision ID: 087
Revises: 086
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("asset_values", sa.Column("source_provenance", postgresql.JSONB(), nullable=True))
    op.create_table(
        "asset_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ux_asset_executions_asset_external", "asset_executions", ["asset_id", "external_id"], unique=True)
    op.create_index("ix_asset_executions_workspace_asset_date", "asset_executions", ["workspace_id", "asset_id", "trade_date"])
    op.create_index("ix_asset_executions_asset_id", "asset_executions", ["asset_id"])
    op.create_index("ix_asset_executions_workspace_id", "asset_executions", ["workspace_id"])


def downgrade():
    op.drop_table("asset_executions")
    op.drop_column("asset_values", "source_provenance")
