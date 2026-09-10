"""Immutable-source security executions, independent of the position ledger."""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, JSON, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssetExecution(Base):
    __tablename__ = "asset_executions"
    __table_args__ = (
        Index("ux_asset_executions_asset_external", "asset_id", "external_id", unique=True),
        Index("ix_asset_executions_workspace_asset_date", "workspace_id", "asset_id", "trade_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True,
    )
    external_id: Mapped[str] = mapped_column(String(255))
    trade_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(40))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
