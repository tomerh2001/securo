import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OperationStatus = Literal["queued", "collecting", "awaiting_verification", "importing", "succeeded", "partial", "failed"]


class ConnectionOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["refresh", "import", "recover"]


class ConnectionVerificationCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


class OperationEvent(BaseModel):
    at: datetime
    stage: OperationStatus
    code: str | None


class OperationResult(BaseModel):
    transactions_added: int = 0
    valuations_added: int = 0
    activities_added: int = 0
    executions_added: int = 0


class ConnectionOperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    connection_id: uuid.UUID
    kind: Literal["refresh", "import", "recover"]
    status: OperationStatus
    message_code: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    source_last_success_before: datetime | None
    source_last_success_after: datetime | None
    imported_at: datetime | None
    result: OperationResult
    events: list[OperationEvent]
    retry_after_seconds: int | None
