"""Describe persisted evidence without claiming that a successful sync is complete history."""
import uuid
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_activity import AssetActivity
from app.models.asset_execution import AssetExecution
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.schemas.account_history import (
    AccountHistoryCoverage,
    AccountHistoryMonth,
    AccountHistoryStream,
)
from app.schemas.investment_account import InvestmentDetails
from app.services import account_service, investment_account_service


def _is_archive(provenance: dict | None) -> bool:
    return isinstance(provenance, dict) and provenance.get("origin") in {
        "sure_archive", "actual_archive",
    }


def _stream(kind, dates, count, coverage, *, contains_archive=False):
    if coverage == "complete":
        availability = "available" if count else "empty"
    elif coverage == "unavailable" and not count:
        availability = "unavailable"
    else:
        availability = "partial"
    known_dates = sorted(d for d in dates if d is not None)
    monthly = Counter(d[:7] for d in known_dates)
    return AccountHistoryStream(
        kind=kind, count=count, first_date=known_dates[0] if known_dates else None,
        last_date=known_dates[-1] if known_dates else None, availability=availability,
        contains_archive=contains_archive,
        monthly_counts=[AccountHistoryMonth(month=month, count=monthly[month])
                        for month in sorted(monthly)],
    )


async def bank_history(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID,
) -> AccountHistoryCoverage:
    account = await account_service.get_account(session, account_id, workspace_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    rows = (await session.execute(select(
        Transaction.date, Transaction.source, Transaction.raw_data,
    ).where(Transaction.account_id == account_id,
            Transaction.workspace_id == workspace_id))).all()
    ordinary = [row for row in rows if row.source != "opening_balance"]
    opening_dates = [row.date.isoformat() for row in rows if row.source == "opening_balance"]
    connected = account.connection_id is not None
    coverage = "partial" if ordinary and connected else "unavailable" if connected else "complete"
    notes = ["bank_balance_date_unavailable", "stored_transaction_dates"]
    if connected:
        notes.append("bank_history_completeness_unknown")
    contains_archive = any(
        _is_archive((row.raw_data or {}).get("source_provenance")) for row in ordinary
    )
    if contains_archive:
        notes.append("contains_archive_history")
    return AccountHistoryCoverage(
        account_id=account.id, account_kind="bank",
        # Account.balance has no provider observation date. Neither a recent
        # transaction nor connection.last_sync_at establishes that date.
        balance_as_of=None,
        opening_balance_date=min(opening_dates) if opening_dates else None,
        streams=[_stream("transactions", [row.date.isoformat() for row in ordinary],
                         len(ordinary), coverage, contains_archive=contains_archive)],
        note_codes=notes,
    )


async def investment_history(
    session: AsyncSession, workspace_id: uuid.UUID, asset_id: uuid.UUID,
) -> AccountHistoryCoverage:
    # The same source-backed-account check used by every investment page also
    # excludes manual assets and accounts in other workspaces.
    asset, _ = await investment_account_service._account_row(session, workspace_id, asset_id)
    metadata = asset.external_metadata or {}
    details = InvestmentDetails.model_validate(metadata["investment_details"])
    values = (await session.scalars(select(AssetValue).where(
        AssetValue.asset_id == asset.id, AssetValue.workspace_id == workspace_id,
    ))).all()
    activity_dates = (await session.scalars(select(AssetActivity.date).where(
        AssetActivity.asset_id == asset.id, AssetActivity.workspace_id == workspace_id,
        AssetActivity.amount != 0,
    ))).all()
    execution_dates = (await session.scalars(select(AssetExecution.trade_date).where(
        AssetExecution.asset_id == asset.id, AssetExecution.workspace_id == workspace_id,
    ))).all()
    contains_archive = any(_is_archive(value.source_provenance) for value in values)
    unverified_dates = any(not value.source_as_of_verified for value in values)
    current_id = metadata.get("current_valuation_id")
    current = next((value for value in values
                    if current_id and value.external_id == current_id), None)
    balance_as_of = None
    if (current is not None and current.source_as_of_verified
            and (current.source_provenance or {}).get("bankObservationVerified") is not False):
        balance_as_of = current.date.isoformat()
    notes = []
    if unverified_dates:
        notes.append("valuation_dates_unverified")
    if contains_archive:
        notes.append("contains_archive_history")
    if balance_as_of is None:
        notes.append("balance_date_unavailable")
    if any(len(value) == 7 for value in activity_dates):
        notes.append("activity_month_precision")
    if details.source.status != "ok":
        notes.append("source_needs_attention")
    for kind in ("valuations", "activities", "executions"):
        if getattr(details.coverage, kind) == "unavailable":
            notes.append(f"{kind}_unavailable")
    value_coverage = details.coverage.valuations
    if unverified_dates and value_coverage == "complete":
        value_coverage = "partial"
    return AccountHistoryCoverage(
        account_id=asset.id, account_kind="investment", balance_as_of=balance_as_of,
        opening_balance_date=None,
        streams=[
            _stream("valuations", [value.date.isoformat() if value.source_as_of_verified else None
                                   for value in values], len(values), value_coverage,
                    contains_archive=contains_archive),
            _stream("activities", list(activity_dates), len(activity_dates),
                    details.coverage.activities),
            _stream("executions", [value.isoformat() for value in execution_dates],
                    len(execution_dates), details.coverage.executions),
        ],
        note_codes=notes,
    )
