"""Account navigation must remain a view over the original investment ledger."""
import copy
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.providers.investment_feed import InvestmentFeedProvider
from app.schemas.investment_feed import InvestmentFeed
from app.services import asset_service, investment_account_service
from app.services.connection_service import handle_oauth_callback
from app.services.investment_feed_service import enrich_account_identifiers, sync_feed
from tests.test_investment_feed import connection, payload


def provider_payload(provider="clal"):
    data = payload()
    data["source"]["provider"] = provider
    product = data["products"][0]
    previous = product["id"]
    product.update(id=f"{provider}:policy-12345678", provider=provider,
                   providerProductId="policy-12345678")
    for rows in (data["valuations"], data["activities"], data["tracks"]):
        for row in rows:
            row["productId"] = product["id"]
            row["id"] = row["id"].replace(previous, product["id"])
    product["currentValuationId"] = data["valuations"][0]["id"]
    return data


async def seed_account(session, test_user, test_workspace, data=None):
    test_user.preferences = {**test_user.preferences, "currency_display": "ILS"}
    conn = await connection(session, test_user, test_workspace)
    await sync_feed(session, conn, InvestmentFeed.model_validate(data or provider_payload()))
    await session.commit()
    asset = await session.scalar(select(Asset).where(Asset.connection_id == conn.id))
    return conn, asset


async def test_account_projection_preserves_ledger_identity_and_total(
    session, test_user, test_workspace, client, auth_headers,
):
    conn, asset = await seed_account(session, test_user, test_workspace)
    conn.display_name = "My pension provider"
    conn.logo_url = "https://example.test/logo.svg"
    bank = Account(user_id=test_user.id, workspace_id=test_workspace.id, name="Everyday",
                   type="checking", balance=Decimal("123.45"), currency="ILS")
    session.add(bank)
    await session.flush()
    payment = Transaction(user_id=test_user.id, workspace_id=test_workspace.id,
                          account_id=bank.id, amount=Decimal("12.50"), currency="ILS",
                          type="debit", date=date(2026, 8, 20), description="Existing payment",
                          source="manual")
    session.add(payment)
    await session.commit()
    value_id = await session.scalar(select(AssetValue.id))
    activity_id = await session.scalar(select(AssetActivity.id))
    group_id = asset.group_id
    before = await asset_service.get_asset_values_at(session, test_workspace.id, by_workspace=True)

    response = await client.get("/api/investment-accounts", headers=auth_headers)
    assert response.status_code == 200
    account = response.json()[0]
    assert account["id"] == str(asset.id)
    assert account["balance"] == account["balance_primary"] == 10000
    assert account["masked_number"] == "5678"
    assert account["provider"] == "clal"
    assert account["product_kind"] == "pension"
    assert account["connection_id"] == str(conn.id)
    assert account["group_id"] == str(group_id)
    assert account["institution_name"] == "My pension provider"
    assert account["institution_logo_url"] == "https://example.test/logo.svg"
    assert account["details"]["valuation_date"] == "2026-08-31"
    assert "external_id" not in account and "credentials" not in account
    detail = await client.get(f"/api/investment-accounts/{asset.id}", headers=auth_headers)
    assert detail.status_code == 200 and detail.json() == account

    # A regular feed refresh keeps the same asset and optional collection group.
    await sync_feed(session, conn, InvestmentFeed.model_validate(provider_payload()))
    assert await session.scalar(select(Asset.id)) == asset.id
    assert await session.scalar(select(AssetValue.id)) == value_id
    assert await session.scalar(select(AssetActivity.id)) == activity_id
    assert await session.scalar(select(AssetGroup.id)) == group_id
    assert await asset_service.get_asset_values_at(session, test_workspace.id, by_workspace=True) == before
    assert await session.scalar(select(func.count()).select_from(Account)) == 1
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 1
    await session.refresh(bank)
    await session.refresh(payment)
    assert bank.balance == Decimal("123.45") and payment.amount == Decimal("12.50")


async def test_second_provider_uses_same_contract_and_separate_identity(
    session, test_user, test_workspace, client, auth_headers,
):
    clal, first = await seed_account(session, test_user, test_workspace)
    second = await connection(session, test_user, test_workspace)
    second.external_id = "another-collector"
    second.institution_name = "Migdal"
    data = provider_payload("migdal")
    await sync_feed(session, second, InvestmentFeed.model_validate(data))
    await session.commit()
    result = await client.get("/api/investment-accounts", headers=auth_headers)
    assert result.status_code == 200
    assert {row["provider"] for row in result.json()} == {"clal", "migdal"}
    assert len({row["id"] for row in result.json()}) == 2
    filtered = await client.get("/api/investment-accounts", params={"connection_id": str(second.id)}, headers=auth_headers)
    assert [row["provider"] for row in filtered.json()] == ["migdal"]
    assert filtered.json()[0]["institution_name"] == "Migdal"
    groups = list((await session.scalars(select(AssetGroup))).all())
    assert {group.external_id for group in groups} == {
        f"{test_workspace.id}:clal:pension", f"{test_workspace.id}:migdal:pension",
    }
    assert {group.name for group in groups} == {"Clal Pension", "Migdal Pension"}
    assert await session.scalar(select(func.count()).select_from(Account)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


@pytest.mark.parametrize("recorded_identity", ["settings", "assets", "conflicting_assets"])
@pytest.mark.parametrize("empty_response", [False, True])
async def test_connection_provider_change_is_rejected_before_any_mutation(
    session, test_user, test_workspace, recorded_identity, empty_response,
):
    conn, asset = await seed_account(session, test_user, test_workspace)
    if recorded_identity == "settings":
        metadata = copy.deepcopy(asset.external_metadata)
        metadata["investment_details"].pop("source")
        asset.external_metadata = metadata
    elif recorded_identity == "assets":
        conn.settings = {key: value for key, value in conn.settings.items() if key != "investment_source"}
    else:
        # Even if connection settings were changed, its saved products retain
        # their provider identity and cannot be relabeled by the next pull.
        conn.settings = {**conn.settings, "investment_source": {"provider": "migdal"}}
    await session.flush()

    async def snapshot():
        return (
            copy.deepcopy(conn.settings),
            (await session.execute(select(Asset.id, Asset.name, Asset.connection_id,
                                          Asset.group_id, Asset.external_metadata))).all(),
            (await session.execute(select(AssetGroup.id, AssetGroup.external_id, AssetGroup.name))).all(),
            (await session.execute(select(AssetValue.id, AssetValue.amount, AssetValue.date))).all(),
            (await session.execute(select(AssetActivity.id, AssetActivity.amount, AssetActivity.date))).all(),
            await session.scalar(select(func.count()).select_from(Account)),
            await session.scalar(select(func.count()).select_from(Transaction)),
        )

    before = await snapshot()
    data = provider_payload("migdal")
    data["products"][0]["name"] = "A different provider account"
    data["valuations"][0]["amount"] = "99999.00"
    if empty_response:
        data.update(products=[], valuations=[], activities=[], tracks=[])
        # Source changes must also fail before the stale-response early return.
        data["generatedAt"] = "2026-09-07T10:00:00Z"
    with pytest.raises(ValueError, match="Investment connection provider changed"):
        await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    await session.flush()
    assert await snapshot() == before


@pytest.mark.parametrize("recorded_identity", ["settings", "assets", "credentials"])
async def test_reconnect_checks_verified_source_before_replacing_saved_connection(
    session, test_user, test_workspace, recorded_identity,
):
    conn, asset = await seed_account(session, test_user, test_workspace)
    if recorded_identity != "settings":
        conn.settings = {key: value for key, value in conn.settings.items() if key != "investment_source"}
    if recorded_identity != "assets":
        metadata = copy.deepcopy(asset.external_metadata)
        metadata["investment_details"].pop("source")
        asset.external_metadata = metadata
    if recorded_identity == "credentials":
        conn.credentials = {**conn.credentials, "source_provider": "clal"}
    provider = InvestmentFeedProvider()
    provider.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(provider_payload()))
    with patch("app.providers.investment_feed.get_settings") as settings:
        settings.return_value.investment_feed_url = "http://collector/investments/v1"
        conn.external_id = (await provider.handle_oauth_callback("existing-collector-token")).external_id
        await session.commit()
        before = (conn.external_id, conn.institution_name, copy.deepcopy(conn.credentials),
                  copy.deepcopy(conn.settings), conn.status, conn.last_sync_at)
        financial_before = (
            (await session.execute(select(Asset.id, Asset.group_id, Asset.external_metadata))).all(),
            (await session.execute(select(AssetValue.id, AssetValue.amount))).all(),
            (await session.execute(select(AssetActivity.id, AssetActivity.amount))).all(),
        )
        provider.get_investment_feed.return_value = InvestmentFeed.model_validate(provider_payload("migdal"))
        with patch("app.services.connection_service.get_provider", return_value=provider):
            with pytest.raises(ValueError, match="Investment connection provider changed"):
                await handle_oauth_callback(
                    session, test_workspace.id, test_user.id, "replacement-collector-token",
                    provider_name="investment_feed", reconnect_connection_id=conn.id,
                )
            await session.flush()
            await session.refresh(conn)
            assert (conn.external_id, conn.institution_name, conn.credentials,
                    conn.settings, conn.status, conn.last_sync_at) == before

            provider.get_investment_feed.return_value = InvestmentFeed.model_validate(provider_payload())
            provider.get_investment_feed.reset_mock()
            result = await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "replacement-collector-token",
                provider_name="investment_feed", reconnect_connection_id=conn.id,
            )
            assert result.id == conn.id and result.external_id == before[0]
            assert result.institution_name == "Clal"
            assert result.credentials == {"token": "replacement-collector-token", "source_provider": "clal"}
            provider.get_investment_feed.assert_awaited_once()
    assert (
        (await session.execute(select(Asset.id, Asset.group_id, Asset.external_metadata))).all(),
        (await session.execute(select(AssetValue.id, AssetValue.amount))).all(),
        (await session.execute(select(AssetActivity.id, AssetActivity.amount))).all(),
    ) == financial_before


async def test_missing_foreign_and_non_investment_ids_are_not_accessible(
    session, test_user, test_workspace, client, auth_headers,
):
    conn, asset = await seed_account(session, test_user, test_workspace)
    other_workspace = Workspace(name="Elsewhere", created_by_user_id=test_user.id)
    session.add(other_workspace)
    await session.flush()
    other_conn = await connection(session, test_user, other_workspace)
    await sync_feed(session, other_conn, InvestmentFeed.model_validate(provider_payload()))
    foreign = await session.scalar(select(Asset).where(Asset.workspace_id == other_workspace.id))
    manual = Asset(user_id=test_user.id, workspace_id=test_workspace.id, name="Home",
                   type="real_estate", source="manual", currency="ILS", valuation_method="manual")
    session.add(manual)
    await session.commit()
    for unavailable in (foreign.id, manual.id, uuid.uuid4()):
        for suffix in ("", "/activities"):
            response = await client.get(f"/api/investment-accounts/{unavailable}{suffix}", headers=auth_headers)
            assert response.status_code == 404
    for unavailable in (other_conn.id, uuid.uuid4()):
        response = await client.get("/api/investment-accounts", params={"connection_id": str(unavailable)}, headers=auth_headers)
        assert response.status_code == 404
    listed = await client.get("/api/investment-accounts", headers=auth_headers)
    assert [row["id"] for row in listed.json()] == [str(asset.id)]


async def test_unknown_zero_archived_and_detached_accounts(
    session, test_user, test_workspace, client, auth_headers,
):
    data = provider_payload()
    data["products"][0]["currentValuationId"] = None
    data["valuations"] = []
    conn, asset = await seed_account(session, test_user, test_workspace, data)
    response = await client.get(f"/api/investment-accounts/{asset.id}", headers=auth_headers)
    assert response.json()["balance"] is None and response.json()["balance_primary"] is None
    data = provider_payload()
    data["valuations"][0]["amount"] = "0.00"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    asset.is_archived = True
    asset.connection_id = None
    metadata = dict(asset.external_metadata)
    metadata.pop("masked_number")
    asset.external_metadata = metadata
    await session.commit()
    assert (await client.get("/api/investment-accounts", headers=auth_headers)).json() == []
    archived = (await client.get("/api/investment-accounts", params={"include_archived": True}, headers=auth_headers)).json()
    assert len(archived) == 1 and archived[0]["is_archived"]
    assert archived[0]["balance"] == archived[0]["balance_primary"] == 0
    assert archived[0]["masked_number"] is None
    assert archived[0]["connection_id"] is None and archived[0]["institution_name"] is None
    assert archived[0]["provider"] == "clal"
    assert (await client.get(f"/api/investment-accounts/{asset.id}", headers=auth_headers)).status_code == 200


async def test_activity_pages_filters_facets_and_month_precision(
    session, test_user, test_workspace, client, auth_headers,
):
    data = provider_payload()
    template = data["activities"][0]
    data["activities"] = []
    for index in range(30):
        row = {**template, "id": f"activity-{index:02}", "sourceId": str(index)}
        if index >= 25:
            row.update(date="2025-12-05", dateKind="booking", kind="management_fee", amount="-12.50")
        data["activities"].append(row)
    data["activities"].append({**template, "id": "cleared", "date": "2024-01", "kind": "other", "amount": "0.00"})
    conn, asset = await seed_account(session, test_user, test_workspace, data)
    base = f"/api/investment-accounts/{asset.id}/activities"
    first = (await client.get(base, headers=auth_headers)).json()
    assert first["total"] == 30 and first["page"] == 1 and first["limit"] == 25
    assert len(first["items"]) == 25
    assert all(row["date"] == "2026-08" and row["date_kind"] == "contribution_month" for row in first["items"])
    assert first["available_years"] == [2026, 2025]
    assert first["available_kinds"] == ["employer_contribution", "management_fee"]
    second = (await client.get(base, params={"page": 2}, headers=auth_headers)).json()
    assert len(second["items"]) == 5
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    fees = (await client.get(base, params={"kind": "management_fee", "year": 2025, "limit": 2}, headers=auth_headers)).json()
    assert fees["total"] == 5 and len(fees["items"]) == 2
    assert all(row["amount"] == -12.5 for row in fees["items"])
    assert fees["available_years"] == first["available_years"]
    assert fees["available_kinds"] == first["available_kinds"]
    empty = (await client.get(base, params={"year": 2024}, headers=auth_headers)).json()
    assert empty["items"] == [] and empty["total"] == 0
    for invalid in ({"page": 0}, {"limit": 101}, {"kind": "made_up"}, {"year": 10000}):
        assert (await client.get(base, params=invalid, headers=auth_headers)).status_code == 422


async def test_account_currency_conversion_uses_shared_fx_service(session, test_user, test_workspace):
    conn, asset = await seed_account(session, test_user, test_workspace)
    session.add(FxRate(base_currency="USD", quote_currency="ILS", date=date.today(), rate=Decimal("4"), source="test"))
    await session.flush()
    account = await investment_account_service.get_account(session, test_workspace.id, asset.id, "USD")
    assert account.balance == 10000 and account.balance_primary == 2500


async def test_missing_exchange_rate_keeps_native_balance_and_primary_unknown(session, test_user, test_workspace):
    conn, asset = await seed_account(session, test_user, test_workspace)
    assert await session.scalar(select(func.count()).select_from(FxRate)) == 0
    with patch("app.services.fx_rate_service.sync_rates", new_callable=AsyncMock) as fetch:
        account = await investment_account_service.get_account(session, test_workspace.id, asset.id, "USD")
        native = await investment_account_service.get_account(session, test_workspace.id, asset.id, "ILS")
    fetch.assert_not_awaited()
    assert account.balance == 10000 and account.balance_primary is None
    assert native.balance == native.balance_primary == 10000


@pytest.mark.parametrize("provider", ["migdal", "meitav_dash", "provider-123"])
async def test_connection_name_comes_from_feed(provider):
    handler = InvestmentFeedProvider()
    handler.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(provider_payload(provider)))
    with patch("app.providers.investment_feed.get_settings") as settings:
        settings.return_value.investment_feed_url = "http://collector/investments/v1"
        result = await handler.handle_oauth_callback("test-access-token-only")
    assert result.institution_name == provider.replace("_", " ").replace("-", " ").title()


@pytest.mark.parametrize("mutate", [
    lambda data: data["source"].update(provider="../provider"),
    lambda data: data["source"].update(provider="Clal"),
    lambda data: data["products"][0].update(provider="migdal"),
    lambda data: data["products"][0].update(id="unnamespaced", currentValuationId=None),
    lambda data: data["products"][0].update(id="clal:", currentValuationId=None),
])
def test_feed_provider_identity_validation(mutate):
    data = provider_payload()
    # Isolate provider validation from cross-row ID validation.
    data.update(valuations=[], activities=[], tracks=[])
    data["products"][0]["currentValuationId"] = None
    mutate(data)
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(data)


async def test_existing_clal_group_and_asset_ids_are_adopted_without_renaming(session, test_user, test_workspace):
    conn, asset = await seed_account(session, test_user, test_workspace)
    group = await session.get(AssetGroup, asset.group_id)
    group.name = "My retirement"
    original = (asset.id, asset.external_id, group.id, group.external_id)
    data = copy.deepcopy(provider_payload())
    data["products"][0]["name"] = "Renamed by provider"
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    assert (asset.id, asset.external_id, group.id, group.external_id) == original
    assert group.name == "My retirement"


async def test_identifier_enrichment_only_changes_mask_metadata(session, test_user, test_workspace):
    conn, asset = await seed_account(session, test_user, test_workspace)
    metadata = dict(asset.external_metadata)
    metadata.pop("masked_number")
    asset.external_metadata = metadata
    await session.flush()
    source_before = copy.deepcopy(conn.settings)
    value_before = (await session.execute(select(AssetValue.id, AssetValue.amount))).all()
    activity_before = (await session.execute(select(AssetActivity.id, AssetActivity.amount))).all()
    feed = InvestmentFeed.model_validate(provider_payload())
    assert await enrich_account_identifiers(session, conn, feed) == 1
    assert asset.external_metadata == {**metadata, "masked_number": "5678"}
    assert await enrich_account_identifiers(session, conn, feed) == 0
    assert conn.settings == source_before
    assert (await session.execute(select(AssetValue.id, AssetValue.amount))).all() == value_before
    assert (await session.execute(select(AssetActivity.id, AssetActivity.amount))).all() == activity_before
    assert await session.scalar(select(func.count()).select_from(Asset)) == 1
