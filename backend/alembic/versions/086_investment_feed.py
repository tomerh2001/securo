"""Source-dated investment valuations and savings activity.

Revision ID: 086
Revises: 085
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("asset_values", sa.Column("external_id", sa.String(255), nullable=True))
    op.add_column("asset_values", sa.Column("source_as_of_verified", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("asset_values", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ux_asset_values_asset_external", "asset_values", ["asset_id", "external_id"], unique=True, postgresql_where=sa.text("external_id IS NOT NULL"))
    op.create_table(
        "asset_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("date_kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ux_asset_activities_asset_external", "asset_activities", ["asset_id", "external_id"], unique=True)
    op.create_index("ix_asset_activities_asset_id", "asset_activities", ["asset_id"])
    op.create_index("ix_asset_activities_workspace_id", "asset_activities", ["workspace_id"])


def downgrade():
    op.drop_table("asset_activities")
    op.drop_index("ux_asset_values_asset_external", table_name="asset_values")
    op.drop_column("asset_values", "observed_at")
    op.drop_column("asset_values", "source_as_of_verified")
    op.drop_column("asset_values", "external_id")
