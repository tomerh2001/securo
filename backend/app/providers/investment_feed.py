"""Pull provider-specific investment feeds from administrator-configured collectors."""
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.providers.base import BankProvider, ConnectionData, SessionExpiredError, SourceControlError
from app.schemas.connection_source import CollectorControlStatus, SourceRefreshResult
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

    @property
    def supports_source_refresh(self) -> bool:
        return True

    @staticmethod
    def configured_external_id(source_provider: str = "clal") -> str:
        endpoint = _endpoint(source_provider)
        return "investment-feed:" + hashlib.sha256(endpoint.encode()).hexdigest()[:24]

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
        # Endpoint identity remains stable across token rotation and isolated by source.
        external_id = self.configured_external_id(source_provider)
        return ConnectionData(external_id, SOURCE_NAMES[source_provider], credentials, [])

    async def get_accounts(self, credentials):
        return []

    async def get_transactions(self, credentials, account_external_id, since=None, payee_source="auto"):
        return []

    async def refresh_credentials(self, credentials):
        return credentials

    async def _control_request(
        self, method: str, action: str, source_provider: str, expected_provider: str | None = None,
    ):
        settings = get_settings()
        # Read capability and control capability are deliberately independent.
        # URL, filesystem path and token are administrator configuration only.
        try:
            # Each source has an independent control capability; never fall back
            # from a second collector to the first collector's credential.
            prefix = "best_invest_feed" if source_provider == "hachshara_best_invest" else "investment_feed"
            token_file = getattr(settings, f"{prefix}_control_token_file")
            token = (Path(token_file).read_text().strip() if token_file
                     else getattr(settings, f"{prefix}_control_token").get_secret_value())
            source_endpoint = _endpoint(source_provider)
            parsed = urlsplit(source_endpoint)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not re.fullmatch(r"[A-Za-z0-9_\-.~]{32,512}", token)):
                raise SourceControlError("source_controls_not_configured")
        except (OSError, ValueError):
            raise SourceControlError("source_controls_not_configured") from None

        endpoint = source_endpoint.rstrip("/") + "/control/" + action
        headers = {"Authorization": f"Bearer {token}"}
        if expected_provider is not None:
            headers["X-Investment-Provider"] = expected_provider
        try:
            # A redirect must never receive the server's independent control token.
            # Streaming bounds memory as well as the model's disclosure surface.
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                async with client.stream(method, endpoint, headers=headers) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise SourceControlError("source_controls_unavailable")
                    if response.status_code == 429:
                        import json
                        retry = json.loads(body).get("retryAfterSeconds", 60)
                        if type(retry) is not int or not 0 <= retry <= 86400:
                            retry = 60
                        raise SourceControlError("source_refresh_rate_limited", status_code=429,
                                                 retry_after_seconds=retry)
                    if response.status_code == 409:
                        raise SourceControlError("source_identity_mismatch", status_code=409)
                    if response.status_code not in ((202,) if method == "POST" else (200,)):
                        raise SourceControlError("source_controls_unavailable")
                    model = SourceRefreshResult if method == "POST" else CollectorControlStatus
                    return model.model_validate_json(body)
        except SourceControlError:
            raise
        except (httpx.HTTPError, ValidationError, ValueError, TypeError, AttributeError):
            raise SourceControlError("source_controls_unavailable") from None

    async def get_source_status(self, credentials: dict) -> CollectorControlStatus:
        return await self._control_request("GET", "status", investment_source_provider(credentials))

    async def request_source_refresh(self, credentials: dict, expected_provider: str) -> SourceRefreshResult:
        source_provider = investment_source_provider(credentials)
        if source_provider != expected_provider:
            raise SourceControlError("source_identity_mismatch", status_code=409)
        return await self._control_request("POST", "refresh", source_provider, expected_provider)

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
