"""Pull provider-specific investment feeds from administrator-configured collectors."""
import hashlib
import re

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.providers.base import BankProvider, ConnectionData, SessionExpiredError
from app.schemas.investment_feed import InvestmentFeed

SOURCE_NAMES = {"clal": "Clal", "hachshara_best_invest": "Hachshara Best Invest"}
BEST_INVEST_TOKEN_PREFIX = "best-invest."


def investment_source_provider(credentials: dict) -> str:
    # Existing Clal connections may predate the persisted source identifier.
    source = credentials.get("source_provider", "clal")
    if not isinstance(source, str) or source not in SOURCE_NAMES:
        raise ValueError("Unsupported investment source")
    return source


def _endpoint(source_provider: str) -> str:
    settings = get_settings()
    endpoint = (
        settings.best_invest_feed_url
        if source_provider == "hachshara_best_invest"
        else settings.investment_feed_url
    )
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        raise ValueError("Investment collector endpoint is not configured")
    return endpoint


class InvestmentFeedProvider(BankProvider):
    name = "investment_feed"
    flow_type = "token"

    async def get_oauth_url(self, redirect_uri, state, flow_params=None):
        raise NotImplementedError("Paste the collector's investment access token")

    async def handle_oauth_callback(self, code):
        # The prefix selects a fixed administrator endpoint, never a pasted URL.
        if not isinstance(code, str):
            raise ValueError("Invalid investment access token")
        source_provider = "clal"
        if code.startswith(BEST_INVEST_TOKEN_PREFIX):
            source_provider = "hachshara_best_invest"
            code = code[len(BEST_INVEST_TOKEN_PREFIX):]
        if not re.fullmatch(r"[A-Za-z0-9_\-.~]{20,512}", code):
            raise ValueError("Invalid investment access token")
        credentials = {"token": code, "source_provider": source_provider}
        feed = await self.get_investment_feed(credentials)
        if feed.source.provider != source_provider:
            raise ValueError("Investment collector source does not match selected provider")
        # Endpoint identity remains stable across token rotation.
        endpoint = _endpoint(source_provider)
        external_id = "investment-feed:" + hashlib.sha256(endpoint.encode()).hexdigest()[:24]
        return ConnectionData(external_id, SOURCE_NAMES[source_provider], credentials, [])

    async def get_accounts(self, credentials):
        return []

    async def get_transactions(self, credentials, account_external_id, since=None, payee_source="auto"):
        return []

    async def refresh_credentials(self, credentials):
        return credentials

    async def get_investment_feed(self, credentials) -> InvestmentFeed:
        source_provider = investment_source_provider(credentials)
        endpoint = _endpoint(source_provider)
        token = credentials.get("token")
        if not isinstance(token, str) or not token:
            raise SessionExpiredError("Investment access token is missing")
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                response = await client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
            if response.status_code in (401, 403):
                raise SessionExpiredError("Investment access token was rejected")
            response.raise_for_status()
            if len(response.content) > 32 * 1024 * 1024:
                raise ValueError("Investment feed is too large")
            feed = InvestmentFeed.model_validate(response.json())
            if feed.source.provider != source_provider:
                raise ValueError("Investment collector source does not match selected provider")
            return feed
        except SessionExpiredError:
            raise
        except (httpx.HTTPError, ValidationError, ValueError):
            # Never expose provider responses, credentials or request headers.
            raise ValueError("Could not read a valid investment feed") from None
