import uuid
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class AssetValue(Base):
    __tablename__ = "asset_values"
    __table_args__ = (
        Index(
            "ux_asset_values_asset_external", "asset_id", "external_id", unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=6))
    # Per-share price on `date` for market-priced holdings (quantity-independent).
    # The value chart is rebuilt as ledger_quantity(date) × price(date) so that
    # entering past buys/sells correctly reshapes the whole history (issue:
    # backdated trades didn't update the baked `amount`). Null for manual/growth
    # assets, where `amount` is the value directly.
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=18, scale=6), nullable=True)
    date: Mapped[_date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual, rule, sync
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_as_of_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_provenance: Mapped[Optional[dict]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True,
    )

    asset: Mapped["Asset"] = relationship(back_populates="values")
