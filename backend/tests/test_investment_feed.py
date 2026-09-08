import copy
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import uuid

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.investment_feed import InvestmentFeedProvider
from app.providers.base import SessionExpiredError
from app.schemas.investment_feed import InvestmentFeed
from app.services import asset_service
from app.services.investment_feed_service import get_activities, sync_feed
from app.schemas.asset import AssetUpdate, AssetValueCreate, AssetTransactionCreate


def payload():
    return {
        "schemaVersion": 1, "generatedAt": "2026-09-08T10:00:00Z",
        "source": {"provider": "clal", "status": "ok", "lastAttemptAt": "2026-09-08T10:00:00Z",
                   "lastSuccessAt": "2026-09-08T10:00:00Z", "staleAfterHours": 192,
                   "errorCode": None, "inventoryComplete": True},
        "products": [{"id": "clal:example-policy", "provider": "clal", "providerProductId": "example-policy",
                      "kind": "pension", "name": "Example pension", "currency": "ILS",
                      "currentValuationId": "clal:example-policy:valuation:2026-08-31",
                      "liquidity": {"status": "restricted", "availableFrom": None, "availableAmount": None},
                      "coverage": {"valuations": "partial", "activities": "partial", "tracks": "complete"},
                      "forecast": {"monthlyPension": "4500.00", "currency": "ILS", "asOf": "2026-08-31"}}],
        "valuations": [{"id": "clal:example-policy:valuation:2026-08-31", "productId": "clal:example-policy",
                        "asOf": "2026-08-31", "observedAt": "2026-09-08T10:00:00Z", "amount": "10000.00", "currency": "ILS"}],
        "activities": [{"id": "clal:example-policy:activity:one", "productId": "clal:example-policy", "sourceId": "one",
                        "date": "2026-08", "dateKind": "contribution_month", "kind": "employer_contribution",
                        "amount": "500.00", "currency": "ILS", "description": "Employer contribution",
                        "observedAt": "2026-09-08T10:00:00Z"}],
        "tracks": [{"id": "track-one", "productId": "clal:example-policy", "name": "Example track",
                    "allocationPercent": "100", "amount": "10000.00", "currency": "ILS",
                    "asOf": "2026-08-31", "observedAt": "2026-09-08T10:00:00Z"}],
    }


async def connection(session, user, workspace):
    row = BankConnection(user_id=user.id, workspace_id=workspace.id, provider="investment_feed",
                         external_id="test-collector", institution_name="Clal", credentials={"token": "test-access-token-only"})
    session.add(row)
    await session.flush()
    return row


async def test_idempotent_values_activity_and_networth_once(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    feed = InvestmentFeed.model_validate(payload())
    await sync_feed(session, conn, feed)
    await sync_feed(session, conn, feed)
    assert await session.scalar(select(func.count()).select_from(Asset)) == 1
    assert await session.scalar(select(func.count()).select_from(AssetGroup)) == 1
    assert await session.scalar(select(func.count()).select_from(AssetValue)) == 1
    assert await session.scalar(select(func.count()).select_from(AssetActivity)) == 1
    assert await session.scalar(select(func.count()).select_from(Account)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
    asset = await session.scalar(select(Asset))
    value = await session.scalar(select(AssetValue))
    assert value.date == date(2026, 8, 31)
    assert value.amount == Decimal("10000")  # Track and forecast are not additional assets.
    assert value.source_as_of_verified
    read = asset_service._asset_to_read(asset, value, 1)
    assert read.current_value == 10000
    assert read.gain_loss is None  # Contributions are not an invented purchase price/return.
    assert read.investment_details is not None
    assert read.investment_details["forecast"]["monthlyPension"] == "4500.00"
    activity = (await get_activities(session, test_workspace.id))[0]
    assert activity["date"] == "2026-08"
    assert activity["amount"] == Decimal("500")


async def test_renames_same_named_products_and_source_corrections(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    first_id = await session.scalar(select(Asset.id))
    data["products"][0]["name"] = "Renamed pension"
    data["valuations"][0]["amount"] = "10001.25"
    data["activities"][0]["amount"] = "510.50"
    twin = copy.deepcopy(data["products"][0])
    twin["id"], twin["providerProductId"] = "clal:second-policy", "second-policy"
    twin["currentValuationId"] = None
    data["products"].append(twin)
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    assert await session.scalar(select(func.count()).select_from(Asset)) == 2
    assert (await session.get(Asset, first_id)).name == "Renamed pension"
    assert await session.scalar(select(AssetValue.amount)) == Decimal("10001.25")
    assert await session.scalar(select(AssetActivity.amount)) == Decimal("510.50")


async def test_auth_required_empty_feed_preserves_last_values(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    data.update(products=[], valuations=[], activities=[], tracks=[])
    data["source"].update(status="auth_required", errorCode="OTP_REQUIRED", inventoryComplete=False)
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    asset = await session.scalar(select(Asset))
    assert not asset.is_archived
    assert await session.scalar(select(AssetValue.amount)) == Decimal("10000")
    assert asset.external_metadata["investment_details"]["source"]["status"] == "auth_required"
    assert conn.credentials["token"] == "test-access-token-only"


async def test_undated_snapshot_and_later_verified_date(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    data["valuations"][0].update(id="undated", asOf=None)
    data["products"][0]["currentValuationId"] = "undated"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    value = await session.scalar(select(AssetValue))
    assert value.date == date(2026, 9, 8)
    assert not value.source_as_of_verified
    asset = await session.scalar(select(Asset))
    assert asset.external_metadata["investment_details"]["valuation_date"] is None
    # A newly verified August date replaces the superseded September observation.
    data["valuations"][0].update(id="dated", asOf="2026-08-31", amount="10002.00")
    data["products"][0]["currentValuationId"] = "dated"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    assert await session.scalar(select(func.count()).select_from(AssetValue)) == 1
    value = await session.scalar(select(AssetValue))
    assert value.source_as_of_verified
    assert value.date == date(2026, 8, 31)
    assert value.amount == Decimal("10002")


async def test_zero_is_real_unknown_has_no_value_and_workspace_isolation(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    data["valuations"] = []
    data["products"][0]["currentValuationId"] = None
    data["products"][0]["liquidity"]["status"] = "unknown"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    assert await session.scalar(select(func.count()).select_from(AssetValue)) == 0
    data["valuations"] = payload()["valuations"]
    data["products"][0]["currentValuationId"] = data["valuations"][0]["id"]
    data["valuations"][0]["amount"] = "0.00"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    assert await session.scalar(select(AssetValue.amount)) == 0
    assert await get_activities(session, uuid.uuid4()) == []


async def test_new_history_does_not_replace_undated_current_balance(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    data["valuations"][0].update(id="undated", asOf=None)
    data["products"][0]["currentValuationId"] = "undated"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    history = copy.deepcopy(payload()["valuations"][0])
    history.update(id="older-history", asOf="2026-06-30", amount="9000.00", observedAt="2026-09-09T10:00:00Z")
    data["valuations"].append(history)
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    asset = await session.scalar(select(Asset))
    latest = await asset_service._get_latest_value(session, asset.id)
    assert latest is not None
    assert latest.amount == Decimal("10000")
    assert not latest.source_as_of_verified


async def test_source_values_cannot_be_overwritten_by_manual_values_or_trades(session, test_user, test_workspace):
    from app.services.asset_transaction_service import add_transaction
    conn = await connection(session, test_user, test_workspace)
    await sync_feed(session, conn, InvestmentFeed.model_validate(payload()))
    asset = await session.scalar(select(Asset))
    value = await session.scalar(select(AssetValue))
    with pytest.raises(HTTPException, match="read-only"):
        await asset_service.add_asset_value(session, asset.id, test_workspace.id, AssetValueCreate(amount=Decimal("123"), date=date.today()))
    with pytest.raises(HTTPException, match="read-only"):
        await asset_service.delete_asset_value(session, value.id, test_workspace.id)
    with pytest.raises(HTTPException, match="read-only"):
        await asset_service.update_asset(session, asset.id, test_workspace.id, test_user.id, AssetUpdate(purchase_price=Decimal("1")))
    with pytest.raises(HTTPException, match="share trades"):
        await add_transaction(session, asset.id, test_workspace.id, AssetTransactionCreate(kind="buy", quantity=Decimal("1"), price=Decimal("1"), date=date.today()))


async def test_activity_and_valuation_http_contract(session, test_user, test_workspace, client, auth_headers):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    data["valuations"][0]["asOf"] = None
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    asset = await session.scalar(select(Asset))
    await session.commit()
    activities = await client.get("/api/assets/activities", headers=auth_headers)
    assert activities.status_code == 200
    assert activities.json()[0]["date"] == "2026-08"
    assert float(activities.json()[0]["amount"]) == 500
    values = await client.get(f"/api/assets/{asset.id}/values", headers=auth_headers)
    assert values.status_code == 200
    assert values.json()[0]["source_as_of_verified"] is False
    assert values.json()[0]["observed_at"] is not None
    foreign = await client.get("/api/assets/activities", headers={**auth_headers, "X-Workspace-Id": str(uuid.uuid4())})
    assert foreign.status_code in (403, 404)


async def test_current_pointer_controls_all_current_totals_and_null_preserves_it(session, test_user, test_workspace):
    from app.services.asset_group_service import _latest_value_amount
    from app.services.report_service import _asset_value_at
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    data["valuations"][0]["asOf"] = date.today().isoformat()
    undated = copy.deepcopy(data["valuations"][0])
    undated.update(id="undated-current", asOf=None, amount="11000.00")
    data["valuations"].append(undated)
    data["products"][0]["currentValuationId"] = "undated-current"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    asset = await session.scalar(select(Asset))
    # A NULL current pointer in a later history-only refresh preserves it.
    data["products"][0]["currentValuationId"] = None
    data["valuations"] = data["valuations"][:1]
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    read = await asset_service.get_asset(session, asset.id, test_workspace.id)
    assert read is not None and read.current_value == 11000
    assert await _latest_value_amount(session, asset.id) == Decimal("11000")
    totals, _ = await asset_service.get_asset_values_at(session, test_workspace.id, as_of_date=date.today(), by_workspace=True)
    assert totals == {"ILS": 11000}
    assert await _asset_value_at(session, test_workspace.id, date.today(), primary_currency="ILS") == 11000
    test_user.preferences = {**test_user.preferences, "currency_display": "ILS"}
    portfolio = await asset_service.get_portfolio_trend(session, test_workspace.id, test_user.id)
    assert portfolio["total"] == 11000


async def test_older_feed_does_not_roll_back_metadata(session, test_user, test_workspace):
    conn = await connection(session, test_user, test_workspace)
    data = payload()
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    data["generatedAt"] = "2026-09-07T10:00:00Z"
    data["source"]["status"] = "error"
    data["products"][0]["name"] = "Older name"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    assert (await session.scalar(select(Asset))).name == "Example pension"
    assert conn.settings["investment_source"]["status"] == "ok"


async def test_native_callback_leaves_existing_bank_transfer_pairing_untouched(session, test_user, test_workspace):
    from app.providers.base import ConnectionData
    from app.services.connection_service import handle_oauth_callback
    provider = InvestmentFeedProvider()
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData("callback-collector", "Clal", {"token": "test-access-token-only"}, []))
    provider.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(payload()))
    with patch("app.services.connection_service.get_provider", return_value=provider), patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock) as detector:
        await handle_oauth_callback(session, test_workspace.id, test_user.id, "test-access-token-only", provider_name="investment_feed")
    detector.assert_not_called()
    assert await session.scalar(select(func.count()).select_from(Account)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


@pytest.mark.parametrize("mutate", [
    lambda data: data["valuations"][0].update(amount=123.45),
    lambda data: data["valuations"][0].update(amount="NaN"),
    lambda data: data["valuations"][0].update(amount="-1.00"),
    lambda data: data["valuations"][0].update(currency="USD"),
    lambda data: data["valuations"].append(copy.deepcopy(data["valuations"][0])),
    lambda data: data["activities"][0].update(date="2026-08-01"),
    lambda data: data["activities"][0].update(productId="missing"),
    lambda data: data["valuations"][0].update(observedAt="2026-09-08T10:00:00"),
])
def test_invalid_feed_rejected_before_mutation(mutate):
    data = payload()
    mutate(data)
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(data)


async def test_provider_auth_errors_are_sanitized_and_redirects_disabled():
    provider = InvestmentFeedProvider()
    response = httpx.Response(403, request=httpx.Request("GET", "http://collector/investments/v1"), text="secret-body")
    mock_client = AsyncMock()
    mock_client.get.return_value = response
    with patch("app.providers.investment_feed.get_settings") as settings, patch("app.providers.investment_feed.httpx.AsyncClient") as client:
        settings.return_value.investment_feed_url = "http://collector/investments/v1"
        client.return_value.__aenter__.return_value = mock_client
        with pytest.raises(SessionExpiredError, match="token was rejected"):
            await provider.get_investment_feed({"token": "test-access-token-only"})
        assert client.call_args.kwargs["follow_redirects"] is False


async def test_connection_pipeline_does_not_run_generic_holdings(session, test_user, test_workspace):
    from app.services.connection_service import _sync_holdings
    conn = await connection(session, test_user, test_workspace)
    provider = InvestmentFeedProvider()
    provider.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(payload()))
    provider.get_holdings = AsyncMock(side_effect=AssertionError("Wrong sync path"))
    with patch("app.services.connection_service.get_provider", return_value=provider):
        await _sync_holdings(session, test_user.id, conn, conn.credentials)
    assert await session.scalar(select(func.count()).select_from(AssetValue)) == 1
    provider.get_holdings.assert_not_called()
