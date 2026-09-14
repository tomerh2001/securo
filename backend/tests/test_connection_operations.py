"""Account updates persist progress and never rely on an open browser page."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy import select

from app.models.connection_operation import ConnectionOperation
from app.models.workspace import Workspace
from app.schemas.connection_operation import ConnectionOperationRead
from app.schemas.connection_source import ConnectionSourceStatus, SourceRefreshResult
from app.services import connection_operation_service as operations
from tests import test_connection_source_controls as source_fixtures

collector = source_fixtures.collector
source_connection = source_fixtures.source_connection
control_payload = source_fixtures.control_payload


def status(**changes):
    value = control_payload()
    value["import"] = {"lastImportedAt": None}
    for key, patch_value in changes.items():
        value[key].update(patch_value)
    return ConnectionSourceStatus.model_validate(value)


async def test_start_is_deduplicated_and_progress_survives_next_request(
    client, auth_headers, session, source_connection,
):
    path = f"/api/connections/{source_connection.id}/operations"
    with patch("app.tasks.connection_operation_tasks.advance.delay") as dispatch:
        first = await client.post(path, headers=auth_headers, json={"kind": "refresh"})
        second = await client.post(path, headers=auth_headers, json={"kind": "refresh"})
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "queued"
    dispatch.assert_called_once()
    loaded = await client.get(path, headers=auth_headers)
    assert loaded.status_code == 200
    assert loaded.json()[0]["id"] == first.json()["id"]
    assert "lock_token" not in loaded.text and "credentials" not in loaded.text


async def test_reader_cannot_start_and_other_workspace_cannot_read(
    client, viewer_auth_headers, auth_headers, session, source_connection,
):
    path = f"/api/connections/{source_connection.id}/operations"
    assert (await client.get(path, headers=viewer_auth_headers)).status_code == 200
    assert (await client.post(path, headers=viewer_auth_headers, json={"kind": "import"})).status_code == 403
    other_workspace = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=source_connection.user_id)
    session.add(other_workspace)
    await session.flush()
    source_connection.workspace_id = other_workspace.id
    await session.commit()
    assert (await client.get(path, headers=auth_headers)).status_code == 404


async def test_collect_verify_import_and_terminal_replay(session, source_connection, monkeypatch):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "refresh")
    current = status()
    read = AsyncMock(side_effect=lambda *args: current)
    refresh = AsyncMock(return_value=SourceRefreshResult(result="started"))
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", read)
    monkeypatch.setattr(operations.connection_source_service, "request_source_refresh", refresh)

    async def do_import(*args, **kwargs):
        assert kwargs["trigger_provider_refresh"] is False
        source_connection.last_sync_at = datetime.now(timezone.utc)
        return source_connection, 0

    sync = AsyncMock(side_effect=do_import)
    monkeypatch.setattr(operations.connection_service, "sync_connection", sync)
    assert await operations.advance_operation(session, row.id) == 5
    assert row.status == "collecting"
    current = status(collection={"running": True})
    assert await operations.advance_operation(session, row.id) == 5
    assert row.status == "awaiting_verification"
    now = datetime.now(timezone.utc)
    current = status(collection={"running": False, "lastResult": "ok", "lastFinishedAt": now},
                     source={"status": "ok", "lastSuccessAt": now})
    assert await operations.advance_operation(session, row.id) is None
    assert row.status == "succeeded" and row.message_code == "no_new_records"
    assert row.imported_at is not None
    assert [event["stage"] for event in row.events] == ["queued", "collecting", "collecting", "awaiting_verification", "importing", "succeeded"]
    assert await operations.advance_operation(session, row.id) is None
    refresh.assert_awaited_once()
    sync.assert_awaited_once()
    public = ConnectionOperationRead.model_validate(row).model_dump()
    assert set(public["result"]) == {"transactions_added", "valuations_added", "activities_added", "executions_added"}


async def test_failed_collection_does_not_import_old_cache(session, source_connection, monkeypatch):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "refresh")
    row.refresh_dispatched = True
    row.collection_finished_before = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.status = "collecting"
    await session.commit()
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", AsyncMock(return_value=status()))
    sync = AsyncMock()
    monkeypatch.setattr(operations.connection_service, "sync_connection", sync)
    assert await operations.advance_operation(session, row.id) is None
    assert row.status == "failed" and row.message_code == "verification_required"
    sync.assert_not_called()


async def test_duplicate_worker_lease_and_abandoned_operation_timeout(session, source_connection, monkeypatch):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "refresh")
    read = AsyncMock()
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", read)
    row.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    await session.commit()
    assert await operations.advance_operation(session, row.id) is None
    read.assert_not_called()
    row.locked_until = None
    row.requested_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    await session.commit()
    assert await operations.advance_operation(session, row.id) is None
    assert row.status == "failed" and row.message_code == "operation_timed_out"
    assert row.locked_until is None
    read.assert_not_called()


async def test_dispatch_failure_visible_without_false_success(client, auth_headers, session, source_connection):
    with patch("app.tasks.connection_operation_tasks.advance.delay", side_effect=RuntimeError("private broker detail")):
        response = await client.post(f"/api/connections/{source_connection.id}/operations", headers=auth_headers,
                                     json={"kind": "import"})
    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["message_code"] == "worker_unavailable"
    assert "private broker detail" not in response.text
    assert len((await session.scalars(select(ConnectionOperation))).all()) == 1


def recovery_status(operation_id, state, **changes):
    current = status(**changes)
    current.manualVerificationAvailable = True
    from app.schemas.connection_source import SourceRecovery
    current.recovery = SourceRecovery(challengeId=operation_id, state=state,
                                      expiresAt=datetime.now(timezone.utc) + timedelta(minutes=3), errorCode=None)
    return current


async def test_manual_sms_waits_for_matching_challenge_before_import(session, source_connection, monkeypatch):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "recover")
    current = status()
    action = AsyncMock()
    sync = AsyncMock(return_value=(source_connection, 0))
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", AsyncMock(side_effect=lambda *args: current))
    monkeypatch.setattr(operations.connection_source_service, "recovery_action", action)
    monkeypatch.setattr(operations.connection_service, "sync_connection", sync)
    assert await operations.advance_operation(session, row.id) == 5
    assert row.message_code == "verification_starting"
    assert action.await_args_list[-1].args[-1] == row.id
    current = recovery_status(row.id, "awaiting_code")
    assert await operations.advance_operation(session, row.id) == 5
    assert row.status == "awaiting_verification" and row.message_code == "verification_code_required"
    current = recovery_status(row.id, "verifying")
    assert await operations.advance_operation(session, row.id) == 5
    assert row.message_code == "verification_submitted"
    sync.assert_not_awaited()
    now = datetime.now(timezone.utc)
    source_connection.last_sync_at = now
    current = recovery_status(row.id, "complete", collection={"running": False, "lastResult": "ok", "lastFinishedAt": now},
                              source={"status": "ok", "lastSuccessAt": now})
    assert await operations.advance_operation(session, row.id) is None
    assert row.status == "succeeded"
    action.assert_awaited_once()
    sync.assert_awaited_once()


@pytest.mark.parametrize("state,code", [
    ("expired", "verification_expired"), ("canceled", "verification_canceled"), ("failed", "verification_failed"),
])
async def test_manual_recovery_terminal_error_never_imports_cache(session, source_connection, monkeypatch, state, code):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "recover")
    row.refresh_dispatched = True
    await session.commit()
    current = recovery_status(row.id, state)
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", AsyncMock(return_value=current))
    sync = AsyncMock()
    monkeypatch.setattr(operations.connection_service, "sync_connection", sync)
    assert await operations.advance_operation(session, row.id) is None
    assert row.status == "failed" and row.message_code == code
    sync.assert_not_awaited()


async def test_recovery_replays_same_id_after_worker_restart(session, source_connection, monkeypatch):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "recover")
    row.refresh_dispatched = True
    await session.commit()
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", AsyncMock(return_value=status()))
    action = AsyncMock()
    monkeypatch.setattr(operations.connection_source_service, "recovery_action", action)
    assert await operations.advance_operation(session, row.id) == 5
    assert action.await_args_list[-1].args[-1] == row.id


async def test_verification_is_writer_only_validated_ephemeral_and_challenge_scoped(
    client, auth_headers, viewer_auth_headers, session, source_connection, monkeypatch,
):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "recover")
    current = recovery_status(row.id, "awaiting_code")
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", AsyncMock(side_effect=lambda *args: current))
    action = AsyncMock()
    monkeypatch.setattr(operations.connection_source_service, "recovery_action", action)
    path = f"/api/connections/{source_connection.id}/operations/{row.id}/verification"
    assert (await client.post(path, headers=viewer_auth_headers, json={"code": "123456"})).status_code == 403
    assert (await client.delete(path, headers=viewer_auth_headers)).status_code == 403
    for body in ({"code": "12345"}, {"code": 123456}, {"code": "123456", "url": "https://elsewhere.example"}):
        assert (await client.post(path, headers=auth_headers, json=body)).status_code == 422
    assert (await client.post(path + "?code=123456", headers=auth_headers, json={"code": "123456"})).status_code == 422
    action.assert_not_awaited()
    response = await client.post(path, headers=auth_headers, json={"code": "123456"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "123456" not in response.text
    assert action.await_args_list[-1].kwargs == {"code": "123456", "cancel": False}
    await session.refresh(row)
    assert "123456" not in str(row.events) + str(row.result)
    response = await client.delete(path, headers=auth_headers)
    assert response.status_code == 200
    assert action.await_args_list[-1].kwargs == {"code": None, "cancel": True}
    current = recovery_status(uuid.uuid4(), "awaiting_code")
    action.reset_mock()
    assert (await client.post(path, headers=auth_headers, json={"code": "123456"})).status_code == 404
    action.assert_not_awaited()


async def test_partial_collection_preserved_and_counts_only_new_records(session, source_connection, monkeypatch):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, "refresh")
    row.refresh_dispatched = True
    row.collection_finished_before = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await session.commit()
    current = status(collection={"lastResult": "partial"}, source={"status": "partial"})
    monkeypatch.setattr(operations.connection_source_service, "get_source_status", AsyncMock(return_value=current))
    monkeypatch.setattr(operations.connection_service, "sync_connection", AsyncMock(return_value=(source_connection, 0)))
    monkeypatch.setattr(operations, "record_counts", AsyncMock(side_effect=[
        {"transactions_added": 100, "valuations_added": 5, "activities_added": 0, "executions_added": 3},
        {"transactions_added": 102, "valuations_added": 5, "activities_added": 1, "executions_added": 3},
    ]))
    assert await operations.advance_operation(session, row.id) is None
    assert row.status == "partial"
    assert row.result == {"transactions_added": 2, "valuations_added": 0, "activities_added": 1, "executions_added": 0}


@pytest.mark.parametrize('failure,code', [
    ('session', 'source_access_expired'), ('action', 'source_access_expired'), ('rate', 'provider_rate_limited'),
])
async def test_import_rollback_keeps_failure_visible_and_does_not_claim_success(
    session, source_connection, monkeypatch, failure, code,
):
    from app.providers.base import ProviderRateLimited, ProviderUserActionRequired, SessionExpiredError
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, 'import')
    row_id = row.id
    async def fail_after_rollback(*args, **kwargs):
        assert kwargs['raise_on_rate_limit'] is True
        await session.rollback()
        if failure == 'session':
            raise SessionExpiredError('private provider message')
        if failure == 'action':
            raise ProviderUserActionRequired('private provider message', code='OTP_REQUIRED')
        raise ProviderRateLimited('private provider message')
    monkeypatch.setattr(operations.connection_service, 'sync_connection', AsyncMock(side_effect=fail_after_rollback))
    assert await operations.advance_operation(session, row_id) is None
    await session.refresh(row)
    assert row.status == 'failed' and row.message_code == code
    assert row.imported_at is None and row.finished_at is not None
    assert row.locked_until is None
    assert 'private provider' not in str(row.events) + str(row.result)


async def test_lost_broker_ack_cannot_overwrite_a_claimed_operation(session, source_connection):
    row, _ = await operations.start_operation(session, source_connection.id, source_connection.workspace_id,
                                             source_connection.user_id, 'refresh')
    row.lock_token = str(uuid.uuid4())
    row.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    await session.commit()
    await operations.dispatch_failed(session, row)
    assert row.status == 'queued' and row.finished_at is None
    assert row.lock_token is not None
