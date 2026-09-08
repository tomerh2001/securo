"""Pull a scoped investment feed from an administrator-configured collector."""
import hashlib
import re

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.providers.base import BankProvider, ConnectionData, SessionExpiredError
from app.schemas.investment_feed import InvestmentFeed


class InvestmentFeedProvider(BankProvider):
    name = "investment_feed"
    flow_type = "token"

    async def get_oauth_url(self, redirect_uri, state, flow_params=None):
        raise NotImplementedError("Paste the collector's investment access token")

    async def handle_oauth_callback(self, code):
        # The endpoint is administrator-controlled. A pasted token cannot turn
        # this server into an arbitrary URL fetcher or expose a token in a URL.
        if not re.fullmatch(r"[A-Za-z0-9_\-.~]{20,512}", code):
            raise ValueError("Invalid investment access token")
        credentials = {"token": code}
        feed = await self.get_investment_feed(credentials)
        # Carry only the verified source identity into reconnect validation;
        # the endpoint hash alone cannot detect a source switch at that URL.
        credentials["source_provider"] = feed.source.provider
        # Stable across a token rotation. Product IDs remain provider-assigned.
        endpoint = get_settings().investment_feed_url
        external_id = "investment-feed:" + hashlib.sha256(endpoint.encode()).hexdigest()[:24]
        institution_name = feed.source.provider.replace("_", " ").replace("-", " ").title()
        return ConnectionData(external_id, institution_name, credentials, [])

    async def get_accounts(self, credentials):
        return []

    async def get_transactions(self, credentials, account_external_id, since=None, payee_source="auto"):
        return []

    async def refresh_credentials(self, credentials):
        return credentials

    async def get_investment_feed(self, credentials) -> InvestmentFeed:
        endpoint = get_settings().investment_feed_url
        if not endpoint or not endpoint.startswith(("http://", "https://")):
            raise ValueError("Investment collector endpoint is not configured")
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
            return InvestmentFeed.model_validate(response.json())
        except SessionExpiredError:
            raise
        except (httpx.HTTPError, ValidationError, ValueError):
            # Never propagate response bodies, request headers, or tokens to
            # connection error handlers, which are allowed to log exceptions.
            raise ValueError("Could not read a valid investment feed") from None
