"""Versioned, source-dated investment snapshots; no bank transactions or trades."""
import re
import uuid
from urllib.parse import quote
from datetime import date as Date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=255)]
ProviderId = Literal["clal", "hachshara_best_invest", "hapoalim"]
ProductKind = Literal["pension", "keren_hishtalmut", "provident_fund", "investment"]
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


def exact_valuation_amount(value: str) -> str:
    # AssetValue stores six decimal places. Preserve source valuation precision
    # without widening the cent-only activity, liquidity or track contracts.
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:0|[1-9]\d*)\.\d{2,6}", value):
        raise ValueError("Valuation must be a decimal string with two to six fractional digits")
    amount = Decimal(value)
    if (value.startswith("-") and amount == 0) or abs(amount) > Decimal("999999999.99"):
        raise ValueError("Valuation is outside the supported range")
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
    executions: Coverage = "unavailable"


class ArchiveValuationProvenance(FeedModel):
    origin: Literal["sure_archive"]
    sourceEntryId: uuid.UUID
    sourceAccountId: uuid.UUID
    sourceSha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    archiveObservedAt: datetime
    observationBasis: Literal["archive_capture", "archive_read"]
    bankObservationVerified: Literal[False]
    sourceAmount: str

    @field_validator("sourceAmount")
    @classmethod
    def validate_amount(cls, value):
        return exact_valuation_amount(value)

    @field_validator("archiveObservedAt")
    @classmethod
    def validate_observation(cls, value):
        if value.tzinfo is None:
            raise ValueError("Archive observation timestamp requires a timezone")
        return value


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
    provider: ProviderId
    providerProductId: Identifier
    kind: ProductKind
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
    provenance: ArchiveValuationProvenance | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        if Decimal(exact_valuation_amount(value)) < 0:
            raise ValueError("Valuation cannot be negative")
        return value

    @model_validator(mode="after")
    def preserve_source_amount(self):
        if self.provenance is not None and Decimal(self.provenance.sourceAmount) != Decimal(self.amount):
            raise ValueError("Archived valuation must preserve its source amount")
        return self


ExecutionKind = Literal[
    "buy", "sell", "dividend", "interest", "redemption", "transfer_in", "transfer_out",
    "stock_bonus", "other",
]


class InvestmentExecution(FeedModel):
    """Source-reported security activity; never a derived holding or bank debit."""
    id: Identifier
    productId: Identifier
    sourceId: Identifier
    sourceIdKind: Literal["natural_key"]
    kind: ExecutionKind
    securityId: Identifier
    isin: Annotated[str, Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")] | None
    symbol: Annotated[str, Field(min_length=1, max_length=100)] | None
    name: Annotated[str, Field(min_length=1, max_length=500)]
    tradeDate: Date
    valueDate: Date | None
    settlementDate: Date | None
    cancelDate: Date | None
    cancelled: bool
    quantity: str | None
    unitPrice: str | None
    netCashAmount: str | None
    currency: Currency
    settlementNetCashAmount: str | None
    settlementCurrency: Currency
    sourceTradeType: Annotated[str, Field(max_length=400)]
    sourceTransactionType: Annotated[str, Field(max_length=400)]
    sourcePaymentType: Annotated[str, Field(max_length=400)] | None
    observedAt: datetime

    @field_validator("quantity", "unitPrice")
    @classmethod
    def validate_quantity_or_price(cls, value):
        if value is not None and (not isinstance(value, str) or len(value) > 64
                or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,12})?", value)):
            raise ValueError("Quantity and price must be nonnegative decimal strings")
        return value

    @field_validator("netCashAmount", "settlementNetCashAmount")
    @classmethod
    def validate_money(cls, value):
        return exact_money(value) if value is not None else None


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
    provider: ProviderId
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
    executions: list[InvestmentExecution] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity_and_currency(self):
        products = {product.id: product for product in self.products}
        for rows in [self.products, self.valuations, self.activities, self.tracks, self.executions]:
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("Duplicate source identity")
        dated_values: set[tuple[str, Date | None]] = set()
        for row in [*self.valuations, *self.activities, *self.tracks]:
            product = products.get(row.productId)
            if product is None or row.currency != product.currency:
                raise ValueError("Unknown product or inconsistent currency")
            if row.observedAt.tzinfo is None:
                raise ValueError("Observation timestamps require a timezone")
        execution_keys = set()
        for execution in self.executions:
            if execution.productId not in products:
                raise ValueError("Execution identifies an unknown product")
            if execution.observedAt.tzinfo is None:
                raise ValueError("Observation timestamps require a timezone")
            key = (execution.productId, execution.sourceId)
            if key in execution_keys:
                raise ValueError("Duplicate execution source identity")
            execution_keys.add(key)
            encoded_source_id = quote(execution.sourceId, safe="~()*!'")
            if (not re.fullmatch(r"natural-key-v1:[a-f0-9]{64}", execution.sourceId)
                    or execution.id != f"{execution.productId}:execution:{encoded_source_id}"):
                raise ValueError("Execution identity must match its product and source identity")
        for row in self.valuations:
            if row.provenance is not None and (
                self.source.provider != "hapoalim" or row.asOf is None
                or row.id != f"sure:entry:{row.provenance.sourceEntryId}"
                or row.observedAt != row.provenance.archiveObservedAt
                or products[row.productId].currentValuationId == row.id
            ):
                raise ValueError("Archive valuation must remain historical with its original source identity")
            key = (row.productId, row.asOf)
            if key in dated_values:
                raise ValueError("Multiple valuations for the same source date")
            dated_values.add(key)
        values_by_id = {row.id: row for row in self.valuations}
        for product in self.products:
            if product.provider != self.source.provider:
                raise ValueError("Product provider must match the feed source")
            if not product.id.startswith(f"{product.provider}:") or product.id == f"{product.provider}:":
                raise ValueError("Product identity must be namespaced by its provider")
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
