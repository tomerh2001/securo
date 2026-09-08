"""Exercise the native auth boundary and collector protocol without any provider login."""
import copy
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.workspace import Workspace
from app.providers import register_provider
from app.providers.base import SourceControlError
from app.providers.investment_feed import InvestmentFeedProvider
import app.providers.investment_feed as provider_module
from app.schemas.investment_feed import InvestmentFeed
from app.services.investment_feed_service import sync_feed
from tests.test_investment_feed import payload


def control_payload():
    return {
        "schemaVersion": 1, "observedAt": "2026-09-09T10:00:00Z",
        "source": {**payload()["source"], "status": "auth_required", "errorCode": "OTP_REQUIRED"},
        "collection": {"running": False, "lastResult": "auth_required",
                       "lastStartedAt": "2026-09-09T09:00:00Z", "lastFinishedAt": "2026-09-09T09:00:10Z"},
        "schedule": {"enabled": True, "expression": "0 7 * * 1", "description": "Every Monday at 07:00",
                     "timezone": "Asia/Jerusalem", "nextRunAt": "2026-09-14T04:00:00Z"},
        "automaticOtp": {"enabled": True, "ready": False, "reason": "phone_unavailable", "nextAllowedAt": None},
        "session": {"status": "auth_required", "lastCheckedAt": "2026-09-09T09:00:00Z",
                    "lastRenewedAt": None, "expiresAt": None, "errorCode": "OTP_REQUIRED",
                    "observedAt": "2026-09-09T10:00:00Z", "keepAliveEnabled": False,
                    "keepAliveMinutes": 0, "expired": False, "overdue": False, "verifiedActive": False},
    }


@pytest.fixture
def collector(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "investment_feed_url", "http://collector.test/investments/v1")
    monkeypatch.setattr(settings, "investment_feed_control_token", SecretStr("control-secret-for-tests-" + "x" * 32))
    monkeypatch.setattr(settings, "investment_feed_control_token_file", "")
    register_provider("investment_feed", InvestmentFeedProvider)
    state: dict[str, Any] = {"calls": [], "feed": payload(), "status": control_payload(),
             "feed_status": 200, "status_status": 200, "refresh_status": 202,
             "refresh": {"result": "started", "retryAfterSeconds": 0}}

    def respond(request):
        state["calls"].append((request.method, request.url.path, dict(request.headers)))
        if request.url.path == "/investments/v1":
            assert request.headers["authorization"] == "Bearer test-access-token-only"
            return httpx.Response(state["feed_status"], json=state["feed"])
        assert request.headers["authorization"] == "Bearer control-secret-for-tests-" + "x" * 32
        assert request.url.host == "collector.test"
        if request.url.path.endswith("/control/status"):
            return httpx.Response(state["status_status"], json=state["status"],
                                  headers={"Location": "https://evil.example/collect"})
        assert request.url.path.endswith("/control/refresh")
        assert request.headers["x-investment-provider"] == state["status"]["source"]["provider"]
        return httpx.Response(state["refresh_status"], json=state["refresh"])

    original_client = httpx.AsyncClient

    def make_client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(respond))

    # Patch only the provider module, leaving the ASGI/auth fixture client intact.
    monkeypatch.setattr(provider_module, "httpx", SimpleNamespace(AsyncClient=make_client, HTTPError=httpx.HTTPError))
    return state


@pytest.fixture
async def source_connection(session, test_user, test_workspace, collector):
    row = BankConnection(user_id=test_user.id, workspace_id=test_workspace.id,
                         provider="investment_feed", external_id=InvestmentFeedProvider.configured_external_id(),
                         institution_name="Clal", credentials={"token": "test-access-token-only", "source_provider": "clal"},
                         last_sync_at=datetime(2026, 9, 9, 8, tzinfo=timezone.utc))
    session.add(row)
    await session.flush()
    await sync_feed(session, row, InvestmentFeed.model_validate(payload()))
    await session.commit()
    return row


async def test_status_read_is_live_sanitized_and_does_not_import(
    client, auth_headers, session, source_connection, collector,
):
    original_settings = copy.deepcopy(source_connection.settings)
    original_imported_at = source_connection.last_sync_at
    collector["status"]["privateMessages"] = "never display"
    collector["status"]["automaticOtp"]["token"] = "never display"
    response = await client.get(f"/api/connections/{source_connection.id}/source/status", headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"]["status"] == "auth_required"
    assert data["automaticOtp"]["reason"] == "phone_unavailable"
    assert data["schedule"]["nextRunAt"] == "2026-09-14T04:00:00Z"
    assert data["import"]["minimumIntervalMinutes"] == 240
    assert data["import"]["lastImportedAt"].startswith("2026-09-09T08:00:00")
    assert "never display" not in response.text
    assert all(method == "GET" for method, _, _ in collector["calls"])
    await session.refresh(source_connection)
    assert source_connection.settings == original_settings
    assert source_connection.last_sync_at.replace(tzinfo=timezone.utc) == original_imported_at.replace(tzinfo=timezone.utc)


async def test_refresh_requires_write_role_but_viewers_can_read(
    client, viewer_auth_headers, source_connection, collector,
):
    path = f"/api/connections/{source_connection.id}/source"
    assert (await client.get(path + "/status", headers=viewer_auth_headers)).status_code == 200
    collector["calls"].clear()
    response = await client.post(path + "/refresh", headers=viewer_auth_headers, json={})
    assert response.status_code == 403
    assert collector["calls"] == []


async def test_refresh_verifies_read_capability_and_identity_before_separate_control_post(
    client, auth_headers, source_connection, collector,
):
    response = await client.post(f"/api/connections/{source_connection.id}/source/refresh", headers=auth_headers, json={})
    assert response.status_code == 202, response.text
    assert response.json() == {"result": "started", "retryAfterSeconds": 0}
    assert [(method, path) for method, path, _ in collector["calls"]] == [
        ("GET", "/investments/v1"), ("GET", "/investments/v1/control/status"),
        ("POST", "/investments/v1/control/refresh"),
    ]


@pytest.mark.parametrize("field", ["token", "url", "provider", "code"])
async def test_refresh_rejects_caller_supplied_control_data(
    field, client, auth_headers, source_connection, collector,
):
    path = f"/api/connections/{source_connection.id}/source/refresh"
    assert (await client.post(path, headers=auth_headers, json={field: "arbitrary"})).status_code == 422
    assert (await client.post(path + f"?{field}=arbitrary", headers=auth_headers, json={})).status_code == 422
    assert collector["calls"] == []


async def test_foreign_connection_and_unauthenticated_requests_do_not_contact_collector(
    client, auth_headers, session, test_user, source_connection, collector,
):
    other_workspace = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=test_user.id)
    session.add(other_workspace)
    await session.flush()
    source_connection.workspace_id = other_workspace.id
    await session.commit()
    for suffix, method in [("status", client.get), ("refresh", client.post)]:
        path = f"/api/connections/{source_connection.id}/source/{suffix}"
        assert (await method(path, headers=auth_headers)).status_code == 404
        assert (await method(path)).status_code in (401, 403)
    assert collector["calls"] == []


@pytest.mark.parametrize("location", ["credentials", "settings", "asset", "feed", "control", "endpoint", "unverified"])
async def test_source_identity_changes_block_collection(
    location, client, auth_headers, session, source_connection, collector,
):
    if location == "credentials":
        source_connection.credentials = {**source_connection.credentials, "source_provider": "other"}
    elif location == "settings":
        source_connection.settings = {**source_connection.settings, "investment_source": {"provider": "other"}}
    elif location == "asset":
        asset = await session.scalar(select(Asset).where(Asset.connection_id == source_connection.id))
        asset.external_metadata = {**asset.external_metadata, "investment_details": {"source": {"provider": "other"}}}
    elif location == "feed":
        collector["feed"] = {**payload(), "source": {**payload()["source"], "provider": "other"},
                             "products": [], "valuations": [], "activities": [], "tracks": []}
    elif location == "control":
        collector["status"]["source"]["provider"] = "other"
    elif location == "endpoint":
        source_connection.external_id = "another-collector-endpoint"
    else:
        source_connection.credentials = {"token": "test-access-token-only"}
        source_connection.settings = {}
        asset = await session.scalar(select(Asset).where(Asset.connection_id == source_connection.id))
        asset.external_metadata = {}
    await session.commit()
    response = await client.post(f"/api/connections/{source_connection.id}/source/refresh", headers=auth_headers, json={})
    # Unknown provider strings in collector JSON are now rejected by the
    # shared source enum before identity comparison. Both paths must deny writes.
    invalid_source_json = location in ("feed", "control")
    assert response.status_code == (503 if invalid_source_json else 409), response.text
    if invalid_source_json:
        assert response.json()["detail"]["code"] == "source_controls_unavailable"
    else:
        assert response.json()["detail"]["code"].startswith("source_identity_")
    assert not any(method == "POST" for method, _, _ in collector["calls"])


async def test_expired_read_token_cannot_use_server_control_capability(
    client, auth_headers, source_connection, collector,
):
    collector["feed_status"] = 403
    response = await client.post(f"/api/connections/{source_connection.id}/source/refresh", headers=auth_headers, json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_access_expired"
    assert len(collector["calls"]) == 1


@pytest.mark.parametrize("code", [301, 302, 307, 401, 403, 500])
async def test_untrusted_control_responses_and_redirects_are_never_forwarded(code, collector):
    collector["status_status"] = code
    collector["status"] = {"token": "private token", "messages": "private SMS"}
    with pytest.raises(SourceControlError) as raised:
        await InvestmentFeedProvider().get_source_status({})
    assert str(raised.value) == "source_controls_unavailable"
    assert len(collector["calls"]) == 1


async def test_control_token_file_precedence_and_missing_file(monkeypatch, tmp_path, collector):
    token_file = tmp_path / "control-token"
    token_file.write_text(get_settings().investment_feed_control_token.get_secret_value())
    monkeypatch.setattr(get_settings(), "investment_feed_control_token_file", str(token_file))
    monkeypatch.setattr(get_settings(), "investment_feed_control_token", SecretStr("unused"))
    assert (await InvestmentFeedProvider().get_source_status({})).source.provider == "clal"
    token_file.unlink()
    with pytest.raises(SourceControlError, match="source_controls_not_configured"):
        await InvestmentFeedProvider().get_source_status({})


async def test_rate_limit_returns_only_bounded_safe_retry(client, auth_headers, source_connection, collector):
    collector["refresh_status"] = 429
    collector["refresh"] = {"error": "sensitive upstream detail", "retryAfterSeconds": 44, "code": "999999"}
    response = await client.post(f"/api/connections/{source_connection.id}/source/refresh", headers=auth_headers, json={})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "44"
    assert response.json()["detail"] == {"code": "source_refresh_rate_limited", "retryAfterSeconds": 44}
    assert "sensitive" not in response.text and "999999" not in response.text


async def test_unknown_enum_and_oversize_response_fail_closed(collector):
    collector["status"]["automaticOtp"]["reason"] = "private message content"
    with pytest.raises(SourceControlError, match="source_controls_unavailable"):
        await InvestmentFeedProvider().get_source_status({})
    collector["status"] = {"oversized": "x" * 65536}
    assert len(json.dumps(collector["status"])) > 65536
    with pytest.raises(SourceControlError, match="source_controls_unavailable"):
        await InvestmentFeedProvider().get_source_status({})


async def test_other_collector_never_uses_primary_control_capability(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "investment_feed_url", "http://clal.test/investments/v1")
    monkeypatch.setattr(settings, "investment_feed_control_token", SecretStr("clal-control-" + "x" * 32))
    monkeypatch.setattr(settings, "investment_feed_control_token_file", "")
    monkeypatch.setattr(settings, "best_invest_feed_url", "http://best.test/investments/v1")
    monkeypatch.setattr(settings, "best_invest_feed_control_token", SecretStr(""))
    monkeypatch.setattr(settings, "best_invest_feed_control_token_file", "")
    calls = []
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        result = control_payload()
        result["source"]["provider"] = "hachshara_best_invest"
        return httpx.Response(200, json=result)

    monkeypatch.setattr(provider_module, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(respond)),
        HTTPError=httpx.HTTPError,
    ))
    provider = InvestmentFeedProvider()
    credentials = {"source_provider": "hachshara_best_invest", "token": "best-read-token"}
    with pytest.raises(SourceControlError) as error:
        await provider.get_source_status(credentials)
    assert error.value.code == "source_controls_not_configured"
    assert not calls

    monkeypatch.setattr(settings, "best_invest_feed_control_token", SecretStr("best-control-" + "y" * 32))
    status = await provider.get_source_status(credentials)
    assert status.source.provider == "hachshara_best_invest"
    assert len(calls) == 1
    assert calls[0].url.host == "best.test"
    assert calls[0].headers["authorization"] == "Bearer best-control-" + "y" * 32
    assert provider.configured_external_id("hachshara_best_invest") != provider.configured_external_id("clal")
    with pytest.raises(SourceControlError) as error:
        await provider.request_source_refresh(credentials, "clal")
    assert error.value.code == "source_identity_mismatch"
    assert len(calls) == 1


async def test_supported_but_different_controller_source_cannot_refresh(
    client, auth_headers, source_connection, collector,
):
    collector["status"]["source"]["provider"] = "hachshara_best_invest"
    response = await client.post(f"/api/connections/{source_connection.id}/source/refresh", headers=auth_headers, json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_identity_mismatch"
    assert not any(method == "POST" for method, _, _ in collector["calls"])
