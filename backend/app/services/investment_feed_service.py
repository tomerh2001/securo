"""Import verified savings data without manufacturing bank payments or trades."""
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_execution import AssetExecution
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.schemas.investment_feed import InvestmentFeed, InvestmentProduct
from app.services.asset_group_service import ensure_group_for_connection

GROUP_NAMES = {
    "pension": "Pension", "keren_hishtalmut": "Keren Hishtalmut",
    "provident_fund": "Provident Fund", "investment": "Investments",
}


def masked_product_number(product: InvestmentProduct) -> str | None:
    if product.provider == "hapoalim":
        match = re.fullmatch(r"[0-9]+-[0-9]+-([0-9]+):securities", product.providerProductId)
        return match.group(1)[-4:] if match else None
    return product.providerProductId[-4:]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def ensure_provider_identity(connection: BankConnection, assets: list[Asset], provider: str) -> None:
    """Validate a collector source before reconnect or sync changes saved data."""
    recorded_sources = [
        (connection.settings or {}).get("investment_source"),
        {"provider": (connection.credentials or {}).get("source_provider")},
    ]
    recorded_sources.extend(
        ((asset.external_metadata or {}).get("investment_details") or {}).get("source")
        for asset in assets if asset.connection_id == connection.id
    )
    for recorded_source in recorded_sources:
        if not isinstance(recorded_source, dict):
            continue
        recorded_provider = recorded_source.get("provider")
        if isinstance(recorded_provider, str) and recorded_provider and recorded_provider != provider:
            raise ValueError("Investment connection provider changed; use a separate connection")


async def sync_feed(session: AsyncSession, connection: BankConnection, feed: InvestmentFeed) -> None:
    """Upsert source identities atomically; incomplete responses never erase data.

    One valued asset represents each product. Tracks and future monthly
    pensions are informational and cannot be counted a second time. Activity
    is its own ledger; payroll deductions do not create another bank debit.
    """
    # Serialize a scheduled pull with a manual refresh. The response was read
    # before this lock, so reject a response older than the one already applied.
    await session.execute(select(BankConnection).where(BankConnection.id == connection.id)
                          .with_for_update().execution_options(populate_existing=True))
    source = connection.provider
    existing = list((await session.scalars(select(Asset).where(
        Asset.workspace_id == connection.workspace_id, Asset.source == source,
    ))).all())
    # A collector endpoint can be reconfigured, but an existing connection must
    # never relabel its saved products as another provider. Older connections
    # may only have the source identity on their owned assets.
    ensure_provider_identity(connection, existing, feed.source.provider)
    previous_generated_at = (connection.settings or {}).get("investment_feed_generated_at")
    if previous_generated_at and _utc(datetime.fromisoformat(previous_generated_at)) > _utc(feed.generatedAt):
        return
    by_external = {asset.external_id: asset for asset in existing}
    source_details = feed.source.model_dump(mode="json")
    connection.settings = {
        **(connection.settings or {}), "investment_source": source_details,
        "investment_feed_generated_at": feed.generatedAt.isoformat(),
    }

    # An OTP/partial/failed refresh still updates freshness on preserved assets.
    for asset in existing:
        if asset.connection_id == connection.id:
            metadata = dict(asset.external_metadata or {})
            details = dict(metadata.get("investment_details") or {})
            details["source"] = source_details
            metadata["investment_details"] = details
            asset.external_metadata = metadata

    groups = {}
    for product in feed.products:
        asset = by_external.get(product.id)
        if asset is not None and asset.connection_id not in (None, connection.id):
            raise ValueError("This investment product belongs to another active connection")
        if product.kind not in groups:
            groups[product.kind] = await ensure_group_for_connection(
                session, user_id=connection.user_id, connection_id=connection.id,
                workspace_id=connection.workspace_id, source=source,
                external_id=f"{connection.workspace_id}:{product.provider}:{product.kind}",
                default_name=f"{connection.institution_name} {GROUP_NAMES[product.kind]}",
            )
        if asset is None:
            asset = Asset(
                user_id=connection.user_id, workspace_id=connection.workspace_id,
                connection_id=connection.id, external_id=product.id, source=source,
                name=product.name, type="investment", currency=product.currency,
                valuation_method="manual", group_id=groups[product.kind].id,
            )
            session.add(asset)
            await session.flush()
        else:
            if asset.currency != product.currency:
                raise ValueError("Investment product currency changed; reconciliation required")
            asset.name = product.name
            asset.connection_id = connection.id
            # Custom grouping and archived/sold decisions remain user-owned.
            if asset.group_id is None:
                asset.group_id = groups[product.kind].id
            else:
                old_group = await session.get(AssetGroup, asset.group_id)
                if (old_group is not None and old_group.source == source
                        and old_group.connection_id in (None, connection.id)):
                    asset.group_id = groups[product.kind].id

        values = list((await session.scalars(select(AssetValue).where(
            AssetValue.asset_id == asset.id, AssetValue.external_id.isnot(None),
        ))).all())
        by_value_id = {value.external_id: value for value in values}
        source_values = [value for value in feed.valuations if value.productId == product.id]
        current_source = next((value for value in source_values if value.id == product.currentValuationId), None)
        for incoming in source_values:
            value = by_value_id.get(incoming.id)
            if value is not None and value.observed_at and _utc(value.observed_at) > _utc(incoming.observedAt):
                continue
            if value is None:
                value = AssetValue(
                    asset_id=asset.id, workspace_id=connection.workspace_id,
                    external_id=incoming.id, source="sync",
                )
                session.add(value)
                values.append(value)
            value.amount = Decimal(incoming.amount)
            value.date = incoming.asOf or incoming.observedAt.date()
            value.source_as_of_verified = incoming.asOf is not None
            value.observed_at = incoming.observedAt
            value.source_provenance = incoming.provenance.model_dump(mode="json") if incoming.provenance else None

        # Once the source supplies a real date, an older undated observation
        # must not outrank that authoritative value simply because its date
        # was the day we looked. Keep dated history; discard only superseded
        # undated snapshots belonging to this feed.
        if current_source is not None and current_source.asOf is not None:
            newest_observation = _utc(current_source.observedAt)
            for value in list(values):
                if (not value.source_as_of_verified and value.observed_at
                        and _utc(value.observed_at) <= newest_observation):
                    await session.delete(value)
                    values.remove(value)

        previous_current = (asset.external_metadata or {}).get("current_valuation_id")
        current_id = product.currentValuationId or previous_current
        latest = next((value for value in values if value.external_id == current_id), None)
        details = {
            "product_kind": product.kind,
            "liquidity": product.liquidity.model_dump(mode="json"),
            "coverage": product.coverage.model_dump(mode="json"),
            "forecast": product.forecast.model_dump(mode="json") if product.forecast else None,
            "tracks": [track.model_dump(mode="json") for track in feed.tracks if track.productId == product.id],
            "source": source_details,
            "valuation_date": latest.date.isoformat() if latest and latest.source_as_of_verified else None,
            "observed_at": latest.observed_at.isoformat() if latest and latest.observed_at else None,
        }
        previous_reports = (asset.external_metadata or {}).get("investment_details", {}).get("report_summaries")
        if previous_reports is not None or product.reportSummaries is not None:
            reports = {report["id"]: report for report in previous_reports or []}
            for report in product.reportSummaries or []:
                reports[report.id] = report.model_dump(mode="json")
            details["report_summaries"] = [reports[key] for key in sorted(reports)]
        asset.external_metadata = {
            "current_valuation_id": current_id,
            "investment_details": details,
            "masked_number": masked_product_number(product),
        }

        activity_rows = list((await session.scalars(select(AssetActivity).where(
            AssetActivity.asset_id == asset.id,
        ))).all())
        by_activity_id = {row.external_id: row for row in activity_rows}
        for incoming in feed.activities:
            if incoming.productId != product.id:
                continue
            row = by_activity_id.get(incoming.id)
            if row is not None and _utc(row.observed_at) > _utc(incoming.observedAt):
                continue
            if row is None:
                row = AssetActivity(
                    asset_id=asset.id, workspace_id=connection.workspace_id, external_id=incoming.id,
                )
                session.add(row)
            row.source_id = incoming.sourceId
            row.kind = incoming.kind
            row.date = incoming.date
            row.date_kind = incoming.dateKind
            row.amount = Decimal(incoming.amount)
            row.currency = incoming.currency
            row.description = incoming.description
            row.observed_at = incoming.observedAt
        executions = list((await session.scalars(select(AssetExecution).where(
            AssetExecution.asset_id == asset.id, AssetExecution.workspace_id == connection.workspace_id,
        ))).all())
        by_execution_id = {row.external_id: row for row in executions}
        for incoming_execution in feed.executions:
            if incoming_execution.productId != product.id:
                continue
            execution = by_execution_id.get(incoming_execution.id)
            if execution is not None and _utc(execution.observed_at) > _utc(incoming_execution.observedAt):
                continue
            if execution is None:
                execution = AssetExecution(
                    asset_id=asset.id, workspace_id=connection.workspace_id, external_id=incoming_execution.id,
                )
                session.add(execution)
            execution.trade_date = incoming_execution.tradeDate
            execution.kind = incoming_execution.kind
            execution.observed_at = incoming_execution.observedAt
            execution.data = incoming_execution.model_dump(mode="json")
    await session.flush()


async def enrich_account_identifiers(
    session: AsyncSession, connection: BankConnection, feed: InvestmentFeed,
) -> int:
    """Backfill display masks from an already verified feed, without financial writes.

    The connection must already own each matched product. Missing products are
    ignored; this operation cannot create accounts, refresh source status, or
    change valuations/activity. Only the final four identifier characters are
    retained, and opaque asset identities are never interpreted as policy numbers.
    """
    # Share the sync lock so enrichment cannot overwrite metadata from a
    # concurrent collection with an older in-memory copy.
    await session.execute(select(BankConnection).where(BankConnection.id == connection.id)
                          .with_for_update().execution_options(populate_existing=True))
    products = {product.id: product for product in feed.products}
    existing = (await session.scalars(select(Asset).where(
        Asset.workspace_id == connection.workspace_id,
        Asset.connection_id == connection.id,
        Asset.source == connection.provider,
        Asset.external_id.in_(products),
    ).execution_options(populate_existing=True))).all()
    updated = 0
    for asset in existing:
        metadata = dict(asset.external_metadata or {})
        details = metadata.get("investment_details") or {}
        product = products.get(asset.external_id or "")
        if product is None:
            continue
        if (details.get("source") or {}).get("provider") != product.provider:
            raise ValueError("Investment account provider does not match its feed")
        masked_number = masked_product_number(product)
        if metadata.get("masked_number") != masked_number:
            metadata["masked_number"] = masked_number
            asset.external_metadata = metadata
            updated += 1
    await session.flush()
    return updated


async def get_activities(session: AsyncSession, workspace_id: uuid.UUID, asset_id: uuid.UUID | None = None):
    query = select(AssetActivity, Asset.name).join(Asset, AssetActivity.asset_id == Asset.id).where(
        AssetActivity.workspace_id == workspace_id, Asset.workspace_id == workspace_id,
    )
    if asset_id is not None:
        query = query.where(AssetActivity.asset_id == asset_id)
    rows = (await session.execute(query.order_by(AssetActivity.date.desc(), AssetActivity.id))).all()
    return [{
        "id": row.id, "asset_id": row.asset_id, "asset_name": name,
        "kind": row.kind, "date": row.date, "date_kind": row.date_kind,
        "amount": row.amount, "currency": row.currency, "description": row.description,
        "source_id": row.source_id, "observed_at": row.observed_at,
    } for row, name in rows]
