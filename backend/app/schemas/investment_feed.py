"""Versioned, source-dated investment snapshots; no bank transactions or trades."""
import re
from datetime import date as Date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=255)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Coverage = Literal["complete", "partial", "unavailable"]
ActivityKind = Literal[
    "employee_contribution", "employer_contribution", "severance_contribution",
    "withdrawal", "transfer_in", "transfer_out", "management_fee", "insurance_cost",
    "investment_return", "actuarial_adjustment", "other",
]


class FeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def exact_money(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:0|[1-9]\d*)\.\d{2}", value):
        raise ValueError("Money must be a decimal string with two fractional digits")
    if value == "-0.00" or abs(Decimal(value)) > Decimal("999999999.99"):
        raise ValueError("Money is outside the supported range")
    return value


class Liquidity(FeedModel):
    status: Literal["restricted", "available", "partially_available", "unknown"]
    availableFrom: Date | None
    availableAmount: str | None

    @field_validator("availableAmount")
    @classmethod
    def validate_amount(cls, value):
        if value is not None and Decimal(exact_money(value)) < 0:
            raise ValueError("Available amount cannot be negative")
        return value


class ProductCoverage(FeedModel):
    valuations: Coverage
    activities: Coverage
    tracks: Coverage


class PensionForecast(FeedModel):
    monthlyPension: str
    currency: Currency
    asOf: Date | None

    @field_validator("monthlyPension")
    @classmethod
    def validate_amount(cls, value):
        if Decimal(exact_money(value)) < 0:
            raise ValueError("Forecast cannot be negative")
        return value


class InvestmentReportLine(FeedModel):
    label: Annotated[str, Field(min_length=1, max_length=500)]
    amount: str

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        return exact_money(value)


class InvestmentReportSummary(FeedModel):
    id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=255)]
    fromDate: Date | None
    toDate: Date | None
    lines: list[InvestmentReportLine]

    @model_validator(mode="after")
    def validate_period(self):
        if self.fromDate is not None and self.toDate is not None and self.fromDate > self.toDate:
            raise ValueError("Report period must not end before it begins")
        return self


class InvestmentProduct(FeedModel):
    id: Identifier
    provider: Literal["clal"]
    providerProductId: Identifier
    kind: Literal["pension", "keren_hishtalmut", "provident_fund", "investment"]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    currency: Currency
    liquidity: Liquidity
    currentValuationId: Identifier | None
    coverage: ProductCoverage
    forecast: PensionForecast | None
    reportSummaries: list[InvestmentReportSummary] | None = None

    @model_validator(mode="after")
    def validate_report_ids(self):
        if self.reportSummaries is not None and len({report.id for report in self.reportSummaries}) != len(self.reportSummaries):
            raise ValueError("Duplicate report identity")
        return self


class InvestmentValuation(FeedModel):
    id: Identifier
    productId: Identifier
    asOf: Date | None
    observedAt: datetime
    amount: str
    currency: Currency

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        if Decimal(exact_money(value)) < 0:
            raise ValueError("Valuation cannot be negative")
        return value


class InvestmentActivity(FeedModel):
    id: Identifier
    productId: Identifier
    sourceId: Identifier
    date: str
    dateKind: Literal["effective", "booking", "contribution_month"]
    kind: ActivityKind
    amount: str
    currency: Currency
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    observedAt: datetime

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        return exact_money(value)

    @model_validator(mode="after")
    def date_precision(self):
        if self.dateKind == "contribution_month":
            if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", self.date):
                raise ValueError("Contribution month must retain month precision")
        else:
            Date.fromisoformat(self.date)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.date):
                raise ValueError("Activity date must use ISO date format")
        return self


class InvestmentTrack(FeedModel):
    id: Identifier
    productId: Identifier
    name: Annotated[str, Field(min_length=1, max_length=255)]
    allocationPercent: str | None
    amount: str | None
    currency: Currency
    asOf: Date | None
    observedAt: datetime

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        if value is not None and Decimal(exact_money(value)) < 0:
            raise ValueError("Track value cannot be negative")
        return value

    @field_validator("allocationPercent")
    @classmethod
    def validate_allocation(cls, value):
        if value is not None:
            if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,6})?", value) or Decimal(value) > 100:
                raise ValueError("Invalid allocation percentage")
        return value


class InvestmentSource(FeedModel):
    provider: Literal["clal"]
    status: Literal["ok", "partial", "auth_required", "error", "never_synced"]
    lastAttemptAt: datetime | None
    lastSuccessAt: datetime | None
    staleAfterHours: Annotated[int, Field(gt=0)]
    errorCode: Literal[
        "OTP_REQUIRED", "INVALID_CREDENTIALS", "ACCOUNT_BLOCKED", "TIMEOUT",
        "CREDENTIAL_RESOLUTION_FAILED", "INCOMPLETE_RESPONSE", "INVALID_RESPONSE", "COLLECTION_FAILED",
    ] | None
    inventoryComplete: bool


class InvestmentFeed(FeedModel):
    schemaVersion: Literal[1]
    generatedAt: datetime
    source: InvestmentSource
    products: list[InvestmentProduct]
    valuations: list[InvestmentValuation]
    activities: list[InvestmentActivity]
    tracks: list[InvestmentTrack]

    @model_validator(mode="after")
    def validate_identity_and_currency(self):
        products = {product.id: product for product in self.products}
        for rows in [self.products, self.valuations, self.activities, self.tracks]:
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("Duplicate source identity")
        dated_values: set[tuple[str, Date | None]] = set()
        for row in [*self.valuations, *self.activities, *self.tracks]:
            product = products.get(row.productId)
            if product is None or row.currency != product.currency:
                raise ValueError("Unknown product or inconsistent currency")
            if row.observedAt.tzinfo is None:
                raise ValueError("Observation timestamps require a timezone")
        for row in self.valuations:
            key = (row.productId, row.asOf)
            if key in dated_values:
                raise ValueError("Multiple valuations for the same source date")
            dated_values.add(key)
        values_by_id = {row.id: row for row in self.valuations}
        for product in self.products:
            if product.currentValuationId is not None:
                current = values_by_id.get(product.currentValuationId)
                if current is None or current.productId != product.id:
                    raise ValueError("Current valuation must identify this product's value")
                if current.asOf is not None and any(
                    row.productId == product.id and row.asOf is not None and row.asOf > current.asOf
                    for row in self.valuations
                ):
                    raise ValueError("Current valuation predates later source valuations")
        for timestamp in [self.generatedAt, self.source.lastAttemptAt, self.source.lastSuccessAt]:
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("Source timestamps require a timezone")
        return self
