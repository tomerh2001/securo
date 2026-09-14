"""Record what the provider's reported account amount represents.

Revision ID: 089
Revises: 088
"""
from alembic import op
import sqlalchemy as sa

revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("balance_semantics", sa.String(30), nullable=True))


def downgrade():
    op.drop_column("accounts", "balance_semantics")
