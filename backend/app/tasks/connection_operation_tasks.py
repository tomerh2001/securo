import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.models.connection_operation import ACTIVE_STATUSES, ConnectionOperation
from app.services.connection_operation_service import advance_operation
from app.tasks.sync_tasks import _make_session_maker
from app.worker import celery_app


async def _advance(operation_id):
    engine, sessions = _make_session_maker()
    try:
        async with sessions() as session:
            return await advance_operation(session, uuid.UUID(operation_id))
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.connection_operation_tasks.advance", soft_time_limit=780, time_limit=840)
def advance(operation_id):
    delay = asyncio.run(_advance(operation_id))
    if delay is not None:
        advance.apply_async(args=[operation_id], countdown=delay)


async def _recoverable():
    engine, sessions = _make_session_maker()
    try:
        async with sessions() as session:
            return [str(value) for value in (await session.scalars(select(ConnectionOperation.id).where(
                ConnectionOperation.status.in_(ACTIVE_STATUSES),
                or_(ConnectionOperation.locked_until.is_(None),
                    ConnectionOperation.locked_until < datetime.now(timezone.utc)),
            ).limit(100))).all()]
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.connection_operation_tasks.recover")
def recover():
    for operation_id in asyncio.run(_recoverable()):
        advance.delay(operation_id)
