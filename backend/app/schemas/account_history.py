"""Stored history coverage shared by bank and investment account pages."""
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class AccountHistoryMonth(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    count: int = Field(ge=0)


class AccountHistoryStream(BaseModel):
    kind: Literal["transactions", "valuations", "activities", "executions"]
    count: int = Field(ge=0)
    # Source contribution months retain YYYY-MM; unknown dates remain null.
    first_date: str | None
    last_date: str | None
    availability: Literal["available", "partial", "unavailable", "empty"]
    contains_archive: bool = False
    monthly_counts: list[AccountHistoryMonth] = Field(default_factory=list)


class AccountHistoryCoverage(BaseModel):
    account_id: uuid.UUID
    account_kind: Literal["bank", "investment"]
    balance_as_of: str | None
    opening_balance_date: str | None
    streams: list[AccountHistoryStream]
    note_codes: list[str] = Field(default_factory=list)
