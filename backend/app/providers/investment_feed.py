"""Pull a scoped investment feed from an administrator-configured collector."""
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


class InvestmentFeedProvider(BankProvider):
    name = "investment_feed"
    flow_type = "token"

    @property
    def supports_source_refresh(self) -> bool:
        return True

    @staticmethod
    def configured_external_id() -> str:
        endpoint = get_settings().investment_feed_url
        return "investment-feed:" + hashlib.sha256(endpoint.encode()).hexdigest()[:24]

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
        external_id = self.configured_external_id()
        institution_name = feed.source.provider.replace("_", " ").replace("-", " ").title()
        return ConnectionData(external_id, institution_name, credentials, [])

    async def get_accounts(self, credentials):
        return []

    async def get_transactions(self, credentials, account_external_id, since=None, payee_source="auto"):
        return []

    async def refresh_credentials(self, credentials):
        return credentials

    async def _control_request(self, method: str, action: str, expected_provider: str | None = None):
        settings = get_settings()
        # Read capability and control capability are deliberately independent.
        # URL, filesystem path and token are administrator configuration only.
        try:
            token = (Path(settings.investment_feed_control_token_file).read_text().strip()
                     if settings.investment_feed_control_token_file
                     else settings.investment_feed_control_token.get_secret_value())
            parsed = urlsplit(settings.investment_feed_url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not re.fullmatch(r"[A-Za-z0-9_\-.~]{32,512}", token)):
                raise SourceControlError("source_controls_not_configured")
        except (OSError, ValueError):
            raise SourceControlError("source_controls_not_configured") from None

        endpoint = settings.investment_feed_url.rstrip("/") + "/control/" + action
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
        return await self._control_request("GET", "status")

    async def request_source_refresh(self, credentials: dict, expected_provider: str) -> SourceRefreshResult:
        return await self._control_request("POST", "refresh", expected_provider)

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
