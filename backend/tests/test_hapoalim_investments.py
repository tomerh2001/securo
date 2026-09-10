"""Hapoalim uses its own read/control capabilities and existing investment ledger."""
import copy
import hashlib
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.providers.base import SourceControlError
from app.providers.investment_feed import InvestmentFeedProvider
from app.schemas.investment_feed import InvestmentFeed
from app.services import investment_account_service
from app.services.connection_service import handle_oauth_callback
from app.services.investment_feed_service import masked_product_number, sync_feed
from tests.test_investment_accounts import provider_payload
from tests.test_investment_feed import connection

HAPOALIM_URL = "http://collector.test/investments/hapoalim/v1"
TOKEN = "synthetic-hapoalim-investment-read-token"


def hapoalim_payload():
    data = provider_payload("hapoalim")
    data["products"][0].update(kind="investment", name="Example bank investments", forecast=None)
    data["activities"] = []
    data["tracks"] = []
    return data


@pytest.fixture
def configured_hapoalim(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "investment_feed_url", "http://clal.test/investments/v1")
    monkeypatch.setattr(settings, "best_invest_feed_url", "http://best.test/investments/v1")
    monkeypatch.setattr(settings, "hapoalim_investment_feed_url", HAPOALIM_URL)
    monkeypatch.setattr(settings, "hapoalim_investment_feed_control_token", SecretStr(""))
    monkeypatch.setattr(settings, "hapoalim_investment_feed_control_token_file", "")
    return settings


async def test_hapoalim_token_selects_only_its_configured_endpoint(configured_hapoalim):
    http = AsyncMock()
    http.get.return_value = httpx.Response(
        200, json=hapoalim_payload(), request=httpx.Request("GET", HAPOALIM_URL),
    )
    with patch("app.providers.investment_feed.httpx.AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = http
        result = await InvestmentFeedProvider().handle_oauth_callback("hapoalim." + TOKEN)
        assert result.credentials == {"token": TOKEN, "source_provider": "hapoalim"}
        assert result.institution_name == "Hapoalim Investments"
        assert result.accounts == []
        assert result.external_id == "investment-feed:" + hashlib.sha256(HAPOALIM_URL.encode()).hexdigest()[:24]
        http.get.assert_awaited_once_with(HAPOALIM_URL, headers={"Authorization": "Bearer " + TOKEN})
        assert factory.call_args.kwargs["follow_redirects"] is False


def test_hapoalim_only_configuration_registers_provider(configured_hapoalim, monkeypatch):
    from app import providers

    monkeypatch.setattr(configured_hapoalim, "investment_feed_enabled", True)
    monkeypatch.setattr(configured_hapoalim, "investment_feed_url", "")
    monkeypatch.setattr(configured_hapoalim, "best_invest_feed_url", "")
    with patch.dict(providers._PROVIDERS, {}, clear=True):
        providers._auto_register_providers()
        assert isinstance(providers.get_provider("investment_feed"), InvestmentFeedProvider)


def test_cached_only_hapoalim_has_no_refresh_action(configured_hapoalim, monkeypatch):
    handler = InvestmentFeedProvider()
    assert handler.source_refresh_available({"source_provider": "hapoalim"}) is False
    assert handler.source_refresh_available({"source_provider": "clal"}) is True
    assert handler.source_refresh_available({"source_provider": "hachshara_best_invest"}) is True
    monkeypatch.setattr(configured_hapoalim, "hapoalim_investment_feed_control_token_file", "/configured/control-token")
    assert handler.source_refresh_available({"source_provider": "hapoalim"}) is False


@pytest.mark.parametrize("token", ["hapoalim.", "hapoalim.short", "hapoalim.https://example.test/token"])
async def test_invalid_hapoalim_token_never_fetches(token):
    with patch("app.providers.investment_feed.httpx.AsyncClient") as http:
        with pytest.raises(ValueError, match="Invalid investment access token"):
            await InvestmentFeedProvider().handle_oauth_callback(token)
        http.assert_not_called()


async def test_missing_hapoalim_endpoint_never_falls_back(configured_hapoalim, monkeypatch):
    monkeypatch.setattr(configured_hapoalim, "hapoalim_investment_feed_url", "")
    with patch("app.providers.investment_feed.httpx.AsyncClient") as http:
        with pytest.raises(ValueError, match="endpoint is not configured"):
            await InvestmentFeedProvider().handle_oauth_callback("hapoalim." + TOKEN)
        http.assert_not_called()


@pytest.mark.parametrize("source", ["unknown", "hapoalim.", "../clal", "", None])
async def test_unknown_source_never_routes_to_clal(source, configured_hapoalim):
    with patch("app.providers.investment_feed.httpx.AsyncClient") as http:
        handler = InvestmentFeedProvider()
        with pytest.raises(ValueError, match="Unsupported investment source"):
            await handler.get_investment_feed({"source_provider": source, "token": TOKEN})
        with pytest.raises(ValueError, match="Unsupported investment source"):
            handler.configured_external_id(source)
        http.assert_not_called()


@pytest.mark.parametrize("selected,returned", [
    ("hapoalim", "clal"), ("hapoalim", "hachshara_best_invest"),
    ("clal", "hapoalim"), ("hachshara_best_invest", "hapoalim"),
])
async def test_different_feed_source_is_rejected_even_without_products(
    selected, returned, configured_hapoalim,
):
    data = provider_payload(returned)
    data.update(products=[], valuations=[], activities=[], tracks=[])
    http = AsyncMock()
    http.get.return_value = httpx.Response(200, json=data, request=httpx.Request("GET", HAPOALIM_URL))
    with patch("app.providers.investment_feed.httpx.AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = http
        with pytest.raises(ValueError, match="Could not read a valid investment feed"):
            await InvestmentFeedProvider().get_investment_feed({"source_provider": selected, "token": TOKEN})


async def test_hapoalim_controls_are_rejected_even_with_reserved_credentials(
    configured_hapoalim, monkeypatch, session, test_user, test_workspace, client, auth_headers,
):
    for prefix in ("investment_feed", "best_invest_feed", "hapoalim_investment_feed"):
        monkeypatch.setattr(configured_hapoalim, f"{prefix}_control_token", SecretStr("synthetic-" + "x" * 32))
    handler = InvestmentFeedProvider()
    credentials = {"source_provider": "hapoalim", "token": TOKEN}
    row = await connection(session, test_user, test_workspace)
    row.credentials = credentials
    row.external_id = handler.configured_external_id("hapoalim")
    await session.commit()
    with patch("app.providers.investment_feed.httpx.AsyncClient") as http, patch(
        "app.services.connection_source_service.get_provider", return_value=handler,
    ):
        with pytest.raises(SourceControlError, match="source_controls_unsupported"):
            await handler.get_source_status(credentials)
        with pytest.raises(SourceControlError, match="source_controls_unsupported"):
            await handler.request_source_refresh(credentials, "hapoalim")
        for method, action in ((client.get, "status"), (client.post, "refresh")):
            response = await method(f"/api/connections/{row.id}/source/{action}", headers=auth_headers)
            assert response.status_code == 400
            assert response.json()["detail"]["code"] == "source_controls_unsupported"
        http.assert_not_called()


async def test_historical_only_hapoalim_account_has_no_invented_current_balance(
    session, test_user, test_workspace,
):
    clal = await connection(session, test_user, test_workspace)
    await sync_feed(session, clal, InvestmentFeed.model_validate(provider_payload()))
    clal_asset = await session.scalar(select(Asset).where(Asset.connection_id == clal.id))
    clal_before = copy.deepcopy(clal_asset.external_metadata)
    hapoalim = await connection(session, test_user, test_workspace)
    hapoalim.external_id = "independent-hapoalim-collector"
    hapoalim.institution_name = "Hapoalim Investments"
    hapoalim.credentials = {"source_provider": "hapoalim", "token": TOKEN}
    data = hapoalim_payload()
    data["products"][0]["currentValuationId"] = None
    data["source"].update(status="never_synced", lastAttemptAt=None, lastSuccessAt=None, inventoryComplete=False)
    template = data["valuations"][0]
    data["valuations"] = [
        {**template, "id": f"sure:entry:00000000-0000-0000-0000-{day:012d}", "asOf": f"2026-01-{day:02d}",
         "observedAt": "2026-02-01T10:00:00Z",
         "amount": format(Decimal("123.4567") + Decimal(day) / Decimal("10000"), ".4f"),
         "provenance": {
             "origin": "sure_archive", "sourceEntryId": f"00000000-0000-0000-0000-{day:012d}",
             "sourceAccountId": "00000000-0000-0000-0000-000000001234", "sourceSha256": "a" * 64,
             "archiveObservedAt": "2026-02-01T10:00:00Z", "observationBasis": "archive_capture",
             "bankObservationVerified": False,
             "sourceAmount": format(Decimal("123.4567") + Decimal(day) / Decimal("10000"), ".4f"),
         }}
        for day in range(1, 17)
    ]
    feed = InvestmentFeed.model_validate(data)
    await sync_feed(session, hapoalim, feed)
    await sync_feed(session, hapoalim, feed)
    account = (await investment_account_service.list_accounts(
        session, test_workspace.id, "ILS", connection_id=hapoalim.id,
    ))[0]
    assert account.provider == "hapoalim" and account.balance is None and account.balance_primary is None
    assert account.details.source.lastSuccessAt is None
    assert account.details.valuation_date is None
    values = (await session.scalars(select(AssetValue).where(AssetValue.asset_id == account.id))).all()
    assert len(values) == 16
    expected = {row["id"]: row for row in data["valuations"]}
    assert all(value.amount == Decimal(expected[value.external_id]["amount"]) for value in values)
    assert all(value.date.isoformat() == expected[value.external_id]["asOf"] for value in values)
    assert all(Decimal(value.source_provenance["sourceAmount"]) == value.amount for value in values)
    assert all(value.source_provenance["bankObservationVerified"] is False for value in values)
    assert await session.scalar(select(func.count()).select_from(Asset)) == 2
    assert await session.scalar(select(func.count()).select_from(Account)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
    assert clal_asset.external_metadata == clal_before


@pytest.mark.parametrize("amount", ["0.00", "0.000000", "12.3456", "12.345678", "999999999.99"])
def test_valuation_preserves_supported_source_precision(amount):
    data = hapoalim_payload()
    data["valuations"][0]["amount"] = amount
    assert InvestmentFeed.model_validate(data).valuations[0].amount == amount


@pytest.mark.parametrize("amount", [
    "1", "1.1", "1.1234567", "1e2", "NaN", "01.00", "-0.0000", "-1.00",
    "999999999.990001", 123.4567,
])
def test_valuation_rejects_invalid_precision_negative_or_out_of_range(amount):
    data = hapoalim_payload()
    data["valuations"][0]["amount"] = amount
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(data)


@pytest.mark.parametrize("section", ["activities", "tracks", "liquidity", "forecast", "report"])
def test_valuation_precision_does_not_widen_other_money_fields(section):
    data = provider_payload("hapoalim")
    if section in {"activities", "tracks"}:
        data[section][0]["amount"] = "12.3456"
    elif section == "liquidity":
        data["products"][0]["liquidity"]["availableAmount"] = "12.3456"
    elif section == "forecast":
        data["products"][0]["forecast"]["monthlyPension"] = "12.3456"
    else:
        data["products"][0]["reportSummaries"] = [{
            "id": "report", "title": "Example summary", "fromDate": None, "toDate": None,
            "lines": [{"label": "Example", "amount": "12.3456"}],
        }]
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(data)


@pytest.mark.parametrize("original", ["clal", "hachshara_best_invest"])
async def test_hapoalim_reconnect_cannot_relabel_another_provider(
    original, configured_hapoalim, session, test_user, test_workspace,
):
    row = await connection(session, test_user, test_workspace)
    row.credentials = {"source_provider": original, "token": "unchanged-test-token"}
    before = copy.deepcopy(row.credentials)
    await session.commit()
    handler = InvestmentFeedProvider()
    handler.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(hapoalim_payload()))
    with patch("app.services.connection_service.get_provider", return_value=handler):
        with pytest.raises(ValueError, match="provider changed"):
            await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "hapoalim." + TOKEN,
                provider_name="investment_feed", reconnect_connection_id=row.id,
            )
    assert row.credentials == before


@pytest.mark.parametrize("source,identifier,expected", [
    ("hapoalim", "12-345-00123456:securities", "3456"),
    ("hapoalim", "12-345-123:securities", "123"),
    ("hapoalim", "12-345-1234:other", None),
    ("hapoalim", "12-345-account:securities", None),
    ("hapoalim", "345-1234:securities", None),
    ("clal", "opaque-policy-9876", "9876"),
    ("hachshara_best_invest", "opaque-plan-6789", "6789"),
])
def test_masked_number_uses_the_bank_account_without_product_suffix(source, identifier, expected):
    data = provider_payload(source)
    data["products"][0]["providerProductId"] = identifier
    product = InvestmentFeed.model_validate(data).products[0]
    assert masked_product_number(product) == expected


@pytest.mark.parametrize("mutation", [
    lambda data: data["products"][0].update(currentValuationId=data["valuations"][0]["id"]),
    lambda data: data["valuations"][0].update(asOf=None),
    lambda data: data["valuations"][0].update(id="not-the-original-archive-entry"),
    lambda data: data["valuations"][0].update(observedAt="2026-02-02T10:00:00Z"),
])
def test_archive_values_cannot_gain_current_or_unverified_provenance(mutation):
    data = hapoalim_payload()
    data["products"][0]["currentValuationId"] = None
    data["valuations"][0].update(
        id="sure:entry:00000000-0000-0000-0000-000000000001", asOf="2026-01-01",
        amount="123.4567", observedAt="2026-02-01T10:00:00Z", provenance={
            "origin": "sure_archive", "sourceEntryId": "00000000-0000-0000-0000-000000000001",
            "sourceAccountId": "00000000-0000-0000-0000-000000001234", "sourceSha256": "a" * 64,
            "archiveObservedAt": "2026-02-01T10:00:00Z", "observationBasis": "archive_capture",
            "bankObservationVerified": False, "sourceAmount": "123.4567",
        },
    )
    assert InvestmentFeed.model_validate(data).valuations[0].provenance is not None
    mutation(data)
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(data)
