"""Investment account views over the existing source valuation and activity ledgers."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.investment_feed import (
    ActivityKind,
    InvestmentReportSummary,
    InvestmentSource,
    InvestmentTrack,
    Liquidity,
    PensionForecast,
    ProductCoverage,
    ProductKind,
)


class InvestmentDetails(BaseModel):
    product_kind: ProductKind
    liquidity: Liquidity
    coverage: ProductCoverage
    forecast: PensionForecast | None
    tracks: list[InvestmentTrack]
    source: InvestmentSource
    valuation_date: date | None
    observed_at: datetime | None
    report_summaries: list[InvestmentReportSummary] = Field(default_factory=list)


class InvestmentAccountRead(BaseModel):
    id: uuid.UUID
    name: str
    currency: str
    balance: float | None
    balance_primary: float | None
    product_kind: ProductKind
    masked_number: str | None = Field(default=None, max_length=4)
    connection_id: uuid.UUID | None
    group_id: uuid.UUID | None
    institution_name: str | None
    institution_logo_url: str | None
    provider: str
    is_archived: bool
    details: InvestmentDetails


class InvestmentAccountActivityRead(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    asset_name: str
    kind: ActivityKind
    date: str
    date_kind: str
    amount: float
    currency: str
    description: str | None
    source_id: str
    observed_at: datetime | None


class InvestmentAccountActivitiesRead(BaseModel):
    items: list[InvestmentAccountActivityRead]
    total: int
    page: int
    limit: int
    available_years: list[int]
    available_kinds: list[str]
