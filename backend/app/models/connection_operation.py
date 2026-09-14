"""Durable, workspace-scoped progress for user-requested account updates."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Boolean, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ACTIVE_STATUSES = ("queued", "collecting", "awaiting_verification", "importing")


class ConnectionOperation(Base):
    __tablename__ = "connection_operations"
    __table_args__ = (
        Index("ux_connection_operation_active", "connection_id", unique=True,
              postgresql_where=text("status IN ('queued','collecting','awaiting_verification','importing')"),
              sqlite_where=text("status IN ('queued','collecting','awaiting_verification','importing')")),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_connections.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    message_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_last_success_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_last_success_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[list] = mapped_column(JSON, default=list)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Internal orchestration state never appears in the public schema.
    collection_finished_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_dispatched: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
