"""Sanitized source controls, separate from importing already collected data."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.investment_feed import InvestmentSource


class ControlModel(BaseModel):
    # Explicit fields are the disclosure boundary. Never proxy raw collector JSON.
    model_config = ConfigDict(extra="ignore")


class CollectionStatus(ControlModel):
    running: bool
    lastResult: Literal["ok", "partial", "auth_required", "error", "skipped"] | None
    lastStartedAt: datetime | None
    lastFinishedAt: datetime | None


class CollectionSchedule(ControlModel):
    enabled: bool
    expression: Annotated[str, Field(max_length=256)]
    description: Annotated[str, Field(max_length=500)] | None
    timezone: Annotated[str, Field(max_length=100)]
    nextRunAt: datetime | None


class AutomaticOtpStatus(ControlModel):
    enabled: bool
    ready: bool
    reason: Literal[
        "ready", "not_configured", "connecting", "disconnected", "inactive",
        "phone_unavailable", "phone_syncing", "reauth_required", "unavailable", "rate_limited",
    ]
    nextAllowedAt: datetime | None


class SourceSession(ControlModel):
    status: Literal["unknown", "active", "auth_required", "error"]
    lastCheckedAt: datetime | None
    lastRenewedAt: datetime | None
    expiresAt: datetime | None
    errorCode: Literal[
        "OTP_REQUIRED", "INVALID_CREDENTIALS", "ACCOUNT_BLOCKED", "TIMEOUT",
        "CREDENTIAL_RESOLUTION_FAILED", "INCOMPLETE_RESPONSE", "INVALID_RESPONSE", "COLLECTION_FAILED",
    ] | None
    observedAt: datetime
    keepAliveEnabled: bool
    keepAliveMinutes: Annotated[int, Field(ge=0, le=10)]
    expired: bool
    overdue: bool
    verifiedActive: bool


class CollectorControlStatus(ControlModel):
    schemaVersion: Literal[1]
    observedAt: datetime
    source: InvestmentSource
    collection: CollectionStatus
    schedule: CollectionSchedule
    automaticOtp: AutomaticOtpStatus
    session: SourceSession


class SavedDataImportStatus(ControlModel):
    lastImportedAt: datetime | None
    checkIntervalMinutes: int = 60
    minimumIntervalMinutes: int = 240


class ConnectionSourceStatus(CollectorControlStatus):
    available: Literal[True] = True
    import_status: SavedDataImportStatus = Field(alias="import")


class SourceRefreshResult(ControlModel):
    result: Literal["started", "already_running"]
    retryAfterSeconds: Annotated[int, Field(ge=0, le=86400)] = 0


class SourceRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
