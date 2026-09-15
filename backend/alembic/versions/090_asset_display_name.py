"""Keep user investment labels separate from provider product names.

Revision ID: 090
Revises: 089
"""
from alembic import op
import sqlalchemy as sa

revision = "090"
down_revision = "089"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assets", sa.Column("display_name", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("assets", "display_name")
