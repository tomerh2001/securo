"""Best Invest routes through its own feed without changing Clal assets."""
import copy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.providers.investment_feed import InvestmentFeedProvider
from app.schemas.investment_feed import InvestmentFeed
from app.services import asset_service
from app.services.connection_service import handle_oauth_callback
from app.services.investment_feed_service import sync_feed
from tests.test_investment_accounts import provider_payload
from tests.test_investment_feed import connection

BEST_INVEST = "hachshara_best_invest"
CLAL_URL = "http://collector/investments/v1"
BEST_INVEST_URL = "http://collector/investments/best-invest/v1"
TOKEN = "synthetic-collector-read-token"


def settings():
    return SimpleNamespace(investment_feed_url=CLAL_URL, best_invest_feed_url=BEST_INVEST_URL)


def best_invest_payload():
    data = provider_payload(BEST_INVEST)
    data["products"][0].update(kind="investment", name="Best Invest example policy", forecast=None)
    data["activities"] = []
    return data


@pytest.mark.parametrize("provider,prefix,url,label", [
    ("clal", "", CLAL_URL, "Clal"),
    (BEST_INVEST, "best-invest.", BEST_INVEST_URL, "Hachshara Best Invest"),
])
async def test_branded_token_selects_admin_endpoint_and_strips_prefix(provider, prefix, url, label):
    response = httpx.Response(200, json=provider_payload(provider), request=httpx.Request("GET", url))
    http = AsyncMock()
    http.get.return_value = response
    with patch("app.providers.investment_feed.get_settings", return_value=settings()), patch(
        "app.providers.investment_feed.httpx.AsyncClient"
    ) as factory:
        factory.return_value.__aenter__.return_value = http
        handler = InvestmentFeedProvider()
        result = await handler.handle_oauth_callback(prefix + TOKEN)
        assert result.credentials == {"token": TOKEN, "source_provider": provider}
        assert result.institution_name == label
        assert result.external_id == "investment-feed:" + hashlib.sha256(url.encode()).hexdigest()[:24]
        http.get.assert_awaited_once_with(url, headers={"Authorization": "Bearer " + TOKEN})
        assert factory.call_args.kwargs["follow_redirects"] is False
        rotated = await handler.handle_oauth_callback(prefix + "rotated-synthetic-access-token")
        assert rotated.external_id == result.external_id


async def test_best_invest_only_configuration_registers_collector():
    from app import providers

    configured = SimpleNamespace(
        investment_feed_enabled=True, investment_feed_url="", best_invest_feed_url=BEST_INVEST_URL,
        pluggy_client_id="", pluggy_client_secret="", enable_banking_private_key="",
        enable_banking_private_key_file="", enable_banking_app_id="", simplefin_enabled=False,
    )
    with patch("app.core.config.get_settings", return_value=configured), patch.dict(
        providers._PROVIDERS, {}, clear=True
    ):
        providers._auto_register_providers()
        assert isinstance(providers.get_provider("investment_feed"), InvestmentFeedProvider)


@pytest.mark.parametrize("code", ["best-invest.", "best-invest.short", "best-invest.https://example.test/token"])
async def test_invalid_branded_token_never_fetches(code):
    with patch("app.providers.investment_feed.httpx.AsyncClient") as http:
        with pytest.raises(ValueError, match="Invalid investment access token"):
            await InvestmentFeedProvider().handle_oauth_callback(code)
        http.assert_not_called()


async def test_missing_best_invest_endpoint_never_falls_back_to_clal():
    configured = settings()
    configured.best_invest_feed_url = ""
    with patch("app.providers.investment_feed.get_settings", return_value=configured), patch(
        "app.providers.investment_feed.httpx.AsyncClient"
    ) as http:
        with pytest.raises(ValueError, match="endpoint is not configured"):
            await InvestmentFeedProvider().handle_oauth_callback("best-invest." + TOKEN)
        http.assert_not_called()


@pytest.mark.parametrize("selected,returned", [(BEST_INVEST, "clal"), ("clal", BEST_INVEST)])
async def test_endpoint_cannot_switch_source_even_for_empty_cached_feed(selected, returned):
    data = provider_payload(returned)
    data.update(products=[], valuations=[], activities=[], tracks=[])
    response = httpx.Response(200, json=data, request=httpx.Request("GET", BEST_INVEST_URL))
    http = AsyncMock()
    http.get.return_value = response
    with patch("app.providers.investment_feed.get_settings", return_value=settings()), patch(
        "app.providers.investment_feed.httpx.AsyncClient"
    ) as factory:
        factory.return_value.__aenter__.return_value = http
        with pytest.raises(ValueError, match="Could not read a valid investment feed"):
            await InvestmentFeedProvider().get_investment_feed(
                {"token": TOKEN, "source_provider": selected}
            )


@pytest.mark.parametrize("provider", ["migdal", "best-invest", "../clal", ""])
def test_unsupported_feed_provider_is_rejected(provider):
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(provider_payload(provider))


async def test_best_invest_failure_preserves_its_value_and_clal_freshness(
    session, test_user, test_workspace,
):
    clal = await connection(session, test_user, test_workspace)
    await sync_feed(session, clal, InvestmentFeed.model_validate(provider_payload()))
    best = await connection(session, test_user, test_workspace)
    best.external_id = "separate-best-invest-collector"
    best.institution_name = "Hachshara Best Invest"
    best.credentials = {"token": TOKEN, "source_provider": BEST_INVEST}
    data = best_invest_payload()
    await sync_feed(session, best, InvestmentFeed.model_validate(data))
    await sync_feed(session, best, InvestmentFeed.model_validate(data))
    clal_asset = await session.scalar(select(Asset).where(Asset.connection_id == clal.id))
    best_asset = await session.scalar(select(Asset).where(Asset.connection_id == best.id))
    clal_before = copy.deepcopy(clal_asset.external_metadata)
    totals_before = await asset_service.get_asset_values_at(session, test_workspace.id, by_workspace=True)
    best_id = best_asset.id
    assert await session.scalar(select(func.count()).select_from(Asset)) == 2
    assert await session.scalar(select(func.count()).select_from(AssetValue)) == 2
    # Best Invest's policy tracks are descriptive; no synthetic cash activity.
    assert await session.scalar(select(func.count()).select_from(AssetActivity).where(
        AssetActivity.asset_id == best_id
    )) == 0

    failed = copy.deepcopy(data)
    failed["generatedAt"] = "2026-09-09T10:00:00Z"
    failed["source"].update(status="auth_required", errorCode="OTP_REQUIRED",
                            lastAttemptAt="2026-09-09T10:00:00Z", inventoryComplete=False)
    failed.update(products=[], valuations=[], activities=[], tracks=[])
    await sync_feed(session, best, InvestmentFeed.model_validate(failed))
    assert clal_asset.external_metadata == clal_before
    assert best_asset.id == best_id
    assert best_asset.external_metadata["investment_details"]["source"]["status"] == "auth_required"
    assert best_asset.external_metadata["investment_details"]["source"]["lastSuccessAt"] == data["source"]["lastSuccessAt"]
    assert await asset_service.get_asset_values_at(session, test_workspace.id, by_workspace=True) == totals_before
    assert await session.scalar(select(func.count()).select_from(Account)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


async def test_reconnect_cannot_claim_an_endpoint_owned_by_another_connection(
    session, test_user, test_workspace,
):
    handler = InvestmentFeedProvider()
    handler.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(best_invest_payload()))
    with patch("app.providers.investment_feed.get_settings", return_value=settings()):
        selected = await handler.handle_oauth_callback("best-invest." + TOKEN)
        owner = await connection(session, test_user, test_workspace)
        owner.external_id = selected.external_id
        owner.credentials = selected.credentials
        other = await connection(session, test_user, test_workspace)
        other.external_id = "previous-endpoint"
        other.credentials = {"token": "original-test-token", "source_provider": BEST_INVEST}
        await session.commit()
        before = (other.external_id, copy.deepcopy(other.credentials), other.institution_name)
        with patch("app.services.connection_service.get_provider", return_value=handler):
            with pytest.raises(ValueError, match="already connected"):
                await handle_oauth_callback(
                    session, test_workspace.id, test_user.id, "best-invest." + TOKEN,
                    provider_name="investment_feed", reconnect_connection_id=other.id,
                )
        assert (other.external_id, other.credentials, other.institution_name) == before


async def test_best_invest_connects_and_rotates_alongside_clal_without_duplicate_assets(
    session, test_user, test_workspace,
):
    handler = InvestmentFeedProvider()
    http = AsyncMock()

    async def fetch(url, **kwargs):
        data = best_invest_payload() if url == BEST_INVEST_URL else provider_payload()
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    http.get.side_effect = fetch
    with patch("app.providers.investment_feed.get_settings", return_value=settings()), patch(
        "app.providers.investment_feed.httpx.AsyncClient"
    ) as factory, patch("app.services.connection_service.get_provider", return_value=handler):
        factory.return_value.__aenter__.return_value = http
        clal = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, TOKEN, provider_name="investment_feed",
        )
        clal_before = (copy.deepcopy(clal.credentials), copy.deepcopy(clal.settings))
        best = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "best-invest." + TOKEN,
            provider_name="investment_feed",
        )
        assert best.id != clal.id and best.external_id != clal.external_id
        assert best.institution_name == "Hachshara Best Invest"
        asset_ids = set((await session.scalars(select(Asset.id))).all())
        value_ids = set((await session.scalars(select(AssetValue.id))).all())
        assert len(asset_ids) == len(value_ids) == 2
        with pytest.raises(ValueError, match="already connected"):
            await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "best-invest." + TOKEN,
                provider_name="investment_feed",
            )
        rotated = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "best-invest.rotated-synthetic-access-token",
            provider_name="investment_feed", reconnect_connection_id=best.id,
        )
        assert rotated.id == best.id
        assert rotated.credentials == {
            "token": "rotated-synthetic-access-token", "source_provider": BEST_INVEST,
        }
        assert (clal.credentials, clal.settings) == clal_before
        assert set((await session.scalars(select(Asset.id))).all()) == asset_ids
        assert set((await session.scalars(select(AssetValue.id))).all()) == value_ids
        assert await session.scalar(select(func.count()).select_from(Account)) == 0
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


async def test_empty_legacy_clal_connection_cannot_be_reconnected_as_best_invest(
    session, test_user, test_workspace,
):
    clal = await connection(session, test_user, test_workspace)
    assert "source_provider" not in clal.credentials
    clal.settings = {}
    await session.commit()
    before = (clal.external_id, clal.institution_name, copy.deepcopy(clal.credentials))
    handler = InvestmentFeedProvider()
    handler.get_investment_feed = AsyncMock(return_value=InvestmentFeed.model_validate(best_invest_payload()))
    with patch("app.providers.investment_feed.get_settings", return_value=settings()), patch(
        "app.services.connection_service.get_provider", return_value=handler,
    ):
        with pytest.raises(ValueError, match="Investment connection provider changed"):
            await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "best-invest." + TOKEN,
                provider_name="investment_feed", reconnect_connection_id=clal.id,
            )
    assert (clal.external_id, clal.institution_name, clal.credentials) == before
    assert await session.scalar(select(func.count()).select_from(Asset)) == 0
