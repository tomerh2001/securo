"""Read investment products as accounts without creating a second balance ledger."""
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.bank_connection import BankConnection
from app.schemas.investment_account import (
    InvestmentAccountActivitiesRead,
    InvestmentAccountActivityRead,
    InvestmentAccountRead,
    InvestmentDetails,
)
from app.services.asset_service import _compute_current_value, _get_latest_value
from app.services.fx_rate_service import _resolve_rate


def _account_query(workspace_id: uuid.UUID):
    return select(Asset, BankConnection).outerjoin(
        BankConnection,
        and_(BankConnection.id == Asset.connection_id,
             BankConnection.workspace_id == workspace_id),
    ).where(Asset.workspace_id == workspace_id, Asset.source == "investment_feed")


async def _account_row(session: AsyncSession, workspace_id: uuid.UUID, asset_id: uuid.UUID):
    row = (await session.execute(_account_query(workspace_id).where(Asset.id == asset_id))).first()
    if row is None or not (row[0].external_metadata or {}).get("investment_details"):
        raise HTTPException(status_code=404, detail="Investment account not found")
    return row


async def _to_read(
    session: AsyncSession, asset: Asset, connection: BankConnection | None, primary_currency: str,
) -> InvestmentAccountRead:
    metadata = asset.external_metadata or {}
    details = InvestmentDetails.model_validate(metadata["investment_details"])
    balance = _compute_current_value(asset, await _get_latest_value(session, asset.id))
    balance_primary = None
    if balance is not None:
        rate = await _resolve_rate(session, asset.currency, primary_currency, allow_fetch=False)
        if rate is not None:
            balance_primary = float((Decimal(str(balance)) * rate).quantize(Decimal("0.01")))
    masked_number = metadata.get("masked_number")
    return InvestmentAccountRead(
        id=asset.id, name=asset.name, currency=asset.currency,
        balance=balance, balance_primary=balance_primary, product_kind=details.product_kind,
        masked_number=masked_number[-4:] if isinstance(masked_number, str) and masked_number else None,
        connection_id=connection.id if connection else None, group_id=asset.group_id,
        institution_name=(connection.display_name or connection.institution_name) if connection else None,
        institution_logo_url=connection.logo_url if connection else None,
        provider=details.source.provider, is_archived=asset.is_archived, details=details,
    )


async def list_accounts(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str,
    *, connection_id: uuid.UUID | None = None, include_archived: bool = False,
) -> list[InvestmentAccountRead]:
    if connection_id is not None and await session.scalar(select(BankConnection.id).where(
        BankConnection.id == connection_id, BankConnection.workspace_id == workspace_id,
    )) is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    query = _account_query(workspace_id)
    if connection_id is not None:
        query = query.where(Asset.connection_id == connection_id)
    if not include_archived:
        query = query.where(Asset.is_archived == False, Asset.sell_date.is_(None))
    rows = (await session.execute(query.order_by(Asset.position, Asset.name, Asset.id))).all()
    return [await _to_read(session, asset, connection, primary_currency)
            for asset, connection in rows if (asset.external_metadata or {}).get("investment_details")]


async def get_account(
    session: AsyncSession, workspace_id: uuid.UUID, asset_id: uuid.UUID, primary_currency: str,
) -> InvestmentAccountRead:
    asset, connection = await _account_row(session, workspace_id, asset_id)
    return await _to_read(session, asset, connection, primary_currency)


async def get_activities(
    session: AsyncSession, workspace_id: uuid.UUID, asset_id: uuid.UUID,
    *, page: int = 1, limit: int = 25, kind: str | None = None, year: int | None = None,
) -> InvestmentAccountActivitiesRead:
    asset, _ = await _account_row(session, workspace_id, asset_id)
    scope = [AssetActivity.asset_id == asset.id,
             AssetActivity.workspace_id == workspace_id, AssetActivity.amount != 0]
    # Facets stay stable as filters change, and exclude cleared source corrections.
    activity_year = func.substr(AssetActivity.date, 1, 4)
    years = (await session.scalars(select(activity_year).where(*scope).distinct()
                                   .order_by(activity_year.desc()))).all()
    kinds = (await session.scalars(select(AssetActivity.kind).where(*scope).distinct()
                                   .order_by(AssetActivity.kind))).all()
    filters = list(scope)
    if kind is not None:
        filters.append(AssetActivity.kind == kind)
    if year is not None:
        filters.append(activity_year == str(year))
    total = await session.scalar(select(func.count()).select_from(AssetActivity).where(*filters)) or 0
    rows = (await session.scalars(select(AssetActivity).where(*filters)
                                  .order_by(AssetActivity.date.desc(), AssetActivity.id)
                                  .offset((page - 1) * limit).limit(limit))).all()
    return InvestmentAccountActivitiesRead(
        items=[InvestmentAccountActivityRead.model_validate({
            "id": row.id, "asset_id": asset.id, "asset_name": asset.name, "kind": row.kind,
            "date": row.date, "date_kind": row.date_kind, "amount": float(row.amount),
            "currency": row.currency, "description": row.description, "source_id": row.source_id,
            "observed_at": row.observed_at,
        }) for row in rows],
        total=total, page=page, limit=limit,
        available_years=[int(value) for value in years], available_kinds=list(kinds),
    )
