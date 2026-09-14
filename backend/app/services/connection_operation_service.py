"""Server-owned collection → import flow; polling never requests another login."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_execution import AssetExecution
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.connection_operation import ACTIVE_STATUSES, ConnectionOperation
from app.models.transaction import Transaction
from app.providers.base import SourceControlError, SessionExpiredError, ProviderUserActionRequired, ProviderRateLimited
from app.services import connection_service, connection_source_service

DEADLINE = timedelta(minutes=25)
LEASE = timedelta(minutes=15)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def transition(operation, status, code=None):
    if operation.status != status or operation.message_code != code or not operation.events:
        operation.events = [*(operation.events or []), {
            "at": datetime.now(timezone.utc).isoformat(), "stage": status, "code": code,
        }][-30:]
    operation.status = status
    operation.message_code = code
    if status not in ACTIVE_STATUSES:
        operation.finished_at = datetime.now(timezone.utc)


async def list_operations(session, connection_id, workspace_id):
    if await connection_service.get_connection(session, connection_id, workspace_id) is None:
        raise SourceControlError("connection_not_found", status_code=404)
    return list((await session.scalars(select(ConnectionOperation).where(
        ConnectionOperation.connection_id == connection_id,
        ConnectionOperation.workspace_id == workspace_id,
    ).order_by(ConnectionOperation.requested_at.desc()).limit(10))).all())


async def start_operation(session, connection_id, workspace_id, user_id, kind):
    connection = await session.scalar(select(BankConnection).where(
        BankConnection.id == connection_id, BankConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
        raise SourceControlError("connection_not_found", status_code=404)
    if connection.status == "disconnected":
        raise SourceControlError("connection_disconnected", status_code=409)
    if kind in ("refresh", "recover") and not connection.source_refresh_available:
        raise SourceControlError("source_controls_unsupported", status_code=400)
    active = await session.scalar(select(ConnectionOperation).where(
        ConnectionOperation.connection_id == connection_id,
        ConnectionOperation.status.in_(ACTIVE_STATUSES),
    ))
    if active is not None:
        return active, False
    operation = ConnectionOperation(connection_id=connection_id, workspace_id=workspace_id,
                                    user_id=user_id, kind=kind, status="queued", result={}, events=[])
    transition(operation, "queued")
    session.add(operation)
    await session.commit()
    await session.refresh(operation)
    return operation, True


async def verify_operation(session, connection_id, workspace_id, operation_id, *, code=None, cancel=False):
    """Forward a one-use code only to this operation's provider challenge."""
    if await connection_service.get_connection(session, connection_id, workspace_id) is None:
        raise SourceControlError("connection_not_found", status_code=404)
    operation = await session.scalar(select(ConnectionOperation).where(
        ConnectionOperation.id == operation_id,
        ConnectionOperation.connection_id == connection_id,
        ConnectionOperation.workspace_id == workspace_id,
    ))
    if operation is None:
        raise SourceControlError("operation_not_found", status_code=404)
    if operation.kind != "recover" or operation.status not in ACTIVE_STATUSES:
        raise SourceControlError("recovery_not_waiting", status_code=409)
    control = await connection_source_service.get_source_status(session, connection_id, workspace_id)
    if control.recovery is None or control.recovery.challengeId != operation.id:
        raise SourceControlError("recovery_not_found", status_code=404)
    await connection_source_service.recovery_action(
        session, connection_id, workspace_id, operation.id, code=code, cancel=cancel,
    )
    # The worker owns transitions. Writing here could overwrite its concurrent
    # completion; the next status poll observes the collector's new state.
    await session.refresh(operation)
    return operation


async def dispatch_failed(session, operation):
    # A lost broker acknowledgement can race a worker that already claimed
    # the request. Only an untouched queued request may be marked failed here.
    now = datetime.now(timezone.utc)
    await session.execute(update(ConnectionOperation).where(
        ConnectionOperation.id == operation.id,
        ConnectionOperation.status == "queued",
        ConnectionOperation.started_at.is_(None),
        ConnectionOperation.lock_token.is_(None),
    ).values(status="failed", message_code="worker_unavailable", finished_at=now,
             events=[*(operation.events or []), {"at": now.isoformat(), "stage": "failed", "code": "worker_unavailable"}]))
    await session.commit()
    await session.refresh(operation)


async def record_counts(session, connection):
    account_ids = select(Account.id).where(Account.connection_id == connection.id,
                                          Account.workspace_id == connection.workspace_id)
    asset_ids = select(Asset.id).where(Asset.connection_id == connection.id,
                                      Asset.workspace_id == connection.workspace_id)
    counts = {"transactions_added": await session.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.account_id.in_(account_ids), Transaction.workspace_id == connection.workspace_id,
        Transaction.source != "opening_balance"))}
    for name, model in (("valuations_added", AssetValue), ("activities_added", AssetActivity), ("executions_added", AssetExecution)):
        counts[name] = await session.scalar(select(func.count()).select_from(model).where(
            model.asset_id.in_(asset_ids), model.workspace_id == connection.workspace_id))
    return counts


async def advance_operation(session: AsyncSession, operation_id: uuid.UUID) -> int | None:
    """Advance one bounded step. A lease makes duplicate deliveries harmless."""
    now = datetime.now(timezone.utc)
    token = str(uuid.uuid4())
    claimed = await session.scalar(update(ConnectionOperation).where(
        ConnectionOperation.id == operation_id,
        ConnectionOperation.status.in_(ACTIVE_STATUSES),
        or_(ConnectionOperation.locked_until.is_(None), ConnectionOperation.locked_until < now),
    ).values(lock_token=token, locked_until=now + LEASE).returning(ConnectionOperation.id))
    await session.commit()
    if claimed is None:
        return None
    operation = await session.get(ConnectionOperation, operation_id, populate_existing=True)
    if operation is None:
        return None
    try:
        if now - utc(operation.requested_at) > DEADLINE:
            transition(operation, "failed", "operation_timed_out")
            return None
        connection = await connection_service.get_connection(session, operation.connection_id, operation.workspace_id)
        if connection is None or connection.status == "disconnected":
            transition(operation, "failed", "connection_disconnected")
            return None
        if operation.started_at is None:
            operation.started_at = now

        if operation.kind in ("refresh", "recover") and operation.status != "importing":
            control = await connection_source_service.get_source_status(session, connection.id, operation.workspace_id)
            if not operation.refresh_dispatched:
                operation.source_last_success_before = control.source.lastSuccessAt
                operation.collection_finished_before = control.collection.lastFinishedAt
                # Persist intent before sending: a worker restart must not request a second SMS.
                operation.refresh_dispatched = True
                transition(operation, "collecting", "verification_starting" if operation.kind == "recover" else "requesting_collection")
                await session.commit()
                if operation.kind == "recover":
                    await connection_source_service.recovery_action(session, connection.id, operation.workspace_id, operation.id)
                else:
                    await connection_source_service.request_source_refresh(session, connection.id, operation.workspace_id)
                    transition(operation, "collecting", "collection_requested")
                return 5
            if operation.kind == "recover":
                recovery = control.recovery
                if recovery is None or recovery.challengeId != operation.id:
                    # Replaying this exact UUID is safe after a worker crash:
                    # the collector persists request tombstones, never the OTP.
                    await connection_source_service.recovery_action(session, connection.id, operation.workspace_id, operation.id)
                    return 5
                if recovery.state in ("failed", "canceled", "expired"):
                    code = {"failed": "verification_failed", "canceled": "verification_canceled",
                            "expired": "verification_expired"}[recovery.state]
                    if recovery.errorCode == "RECOVERY_INTERRUPTED":
                        code = "verification_interrupted"
                    transition(operation, "failed", code)
                    return None
                if recovery.state != "complete":
                    waiting = recovery.state in ("awaiting_code", "verifying")
                    transition(operation, "awaiting_verification" if waiting else "collecting",
                               {"starting": "verification_starting", "awaiting_code": "verification_code_required",
                                "verifying": "verification_submitted"}[recovery.state])
                    return 5
            if control.collection.running:
                waiting = control.session.status == "auth_required"
                transition(operation, "awaiting_verification" if waiting else "collecting",
                           "provider_verification" if waiting else "collecting_data")
                return 5
            finished = utc(control.collection.lastFinishedAt)
            baseline = utc(operation.collection_finished_before)
            if finished is None or (baseline is not None and finished <= baseline):
                return 5
            result = control.collection.lastResult
            operation.source_last_success_after = control.source.lastSuccessAt
            if result not in ("ok", "partial"):
                code = ("verification_required" if result == "auth_required" else
                        "collection_skipped" if result == "skipped" else "collection_failed")
                transition(operation, "failed", code)
                return None
            if control.source.lastSuccessAt is None:
                transition(operation, "failed", "no_saved_data")
                return None
            operation.result = {"source_partial": result == "partial" or control.source.status == "partial"}

        transition(operation, "importing", "importing_saved_data")
        await session.commit()
        before = operation.result.get("before_counts")
        if before is None:
            before = await record_counts(session, connection)
            operation.result = {**operation.result, "before_counts": before}
            await session.commit()
        synced, _ = await connection_service.sync_connection(
            session, connection.id, operation.workspace_id, operation.user_id,
            trigger_provider_refresh=False,
            raise_on_rate_limit=True,
        )
        after = await record_counts(session, synced)
        partial = operation.result.get("source_partial", False)
        operation.result = {name: max(0, after[name] - count) for name, count in before.items()}
        operation.imported_at = synced.last_sync_at
        transition(operation, "partial" if partial else "succeeded",
                   "partial_data_imported" if partial else "updated" if any(operation.result.values()) else "no_new_records")
        return None
    except SourceControlError as error:
        await session.rollback()
        operation = await session.get(ConnectionOperation, operation_id, populate_existing=True)
        if operation is not None:
            operation.retry_after_seconds = error.retry_after_seconds or None
            transition(operation, "failed", error.code)
        return None
    except (SessionExpiredError, ProviderUserActionRequired):
        await session.rollback()
        operation = await session.get(ConnectionOperation, operation_id, populate_existing=True)
        if operation is not None:
            transition(operation, "failed", "source_access_expired")
        return None
    except ProviderRateLimited:
        await session.rollback()
        operation = await session.get(ConnectionOperation, operation_id, populate_existing=True)
        if operation is not None:
            transition(operation, "failed", "provider_rate_limited")
        return None
    except Exception:
        # Never persist exception messages: provider responses can contain secrets.
        await session.rollback()
        operation = await session.get(ConnectionOperation, operation_id, populate_existing=True)
        if operation is not None:
            transition(operation, "failed", "update_failed")
        return None
    finally:
        if operation is not None:
            operation.lock_token = None
            operation.locked_until = None
            try:
                await session.commit()
            except StaleDataError:
                # Deleting a connection also deletes its operations. An
                # in-flight provider response must not recreate either row.
                await session.rollback()
                if await session.get(ConnectionOperation, operation_id) is not None:
                    raise
