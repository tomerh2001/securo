"""Workspace-authorized controls for a connection's original verified source."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.providers import get_provider
from app.providers.base import SessionExpiredError, SourceControlError
from app.providers.investment_feed import InvestmentFeedProvider
from app.schemas.connection_source import ConnectionSourceStatus, SourceRefreshResult
from app.services.connection_service import get_connection
from app.services.investment_feed_service import ensure_provider_identity


async def _verified_source(session: AsyncSession, connection_id: uuid.UUID, workspace_id: uuid.UUID):
    connection = await get_connection(session, connection_id, workspace_id)
    if connection is None:
        raise SourceControlError("connection_not_found", status_code=404)
    if connection.status == "disconnected":
        raise SourceControlError("connection_disconnected", status_code=409)
    try:
        provider = get_provider(connection.provider)
    except ValueError:
        raise SourceControlError("source_controls_not_configured") from None
    if not provider.supports_source_refresh:
        raise SourceControlError("source_controls_unsupported", status_code=400)

    credentials = connection.credentials or {}
    verified_provider = None
    if isinstance(provider, InvestmentFeedProvider):
        # A server URL change cannot grant a pre-existing connection control of
        # another collector, even if both happen to use the same provider name.
        if connection.external_id != provider.configured_external_id():
            raise SourceControlError("source_identity_mismatch", status_code=409)
        try:
            # Prove that this connection's own read token still authorizes the
            # source before using the server's more powerful control capability.
            feed = await provider.get_investment_feed(credentials)
        except SessionExpiredError:
            raise SourceControlError("source_access_expired", status_code=409) from None
        except ValueError:
            raise SourceControlError("source_controls_unavailable") from None
        assets = list((await session.scalars(select(Asset).where(
            Asset.workspace_id == workspace_id, Asset.connection_id == connection.id,
        ))).all())
        sources = [credentials.get("source_provider"),
                   (connection.settings or {}).get("investment_source", {}).get("provider")]
        sources.extend((((asset.external_metadata or {}).get("investment_details") or {})
                        .get("source") or {}).get("provider") for asset in assets)
        if not any(isinstance(source, str) and source for source in sources):
            raise SourceControlError("source_identity_unverified", status_code=409)
        try:
            ensure_provider_identity(connection, assets, feed.source.provider)
        except ValueError:
            raise SourceControlError("source_identity_mismatch", status_code=409) from None
        verified_provider = feed.source.provider

    control = await provider.get_source_status(credentials)
    if verified_provider is not None and control.source.provider != verified_provider:
        raise SourceControlError("source_identity_mismatch", status_code=409)
    return connection, provider, control


async def get_source_status(
    session: AsyncSession, connection_id: uuid.UUID, workspace_id: uuid.UUID,
) -> ConnectionSourceStatus:
    connection, _, control = await _verified_source(session, connection_id, workspace_id)
    # Reading live health does not import data, modify freshness or request OTP.
    return ConnectionSourceStatus.model_validate({
        **control.model_dump(), "available": True,
        "import": {"lastImportedAt": connection.last_sync_at,
                   "checkIntervalMinutes": 60, "minimumIntervalMinutes": 240},
    })


async def request_source_refresh(
    session: AsyncSession, connection_id: uuid.UUID, workspace_id: uuid.UUID,
) -> SourceRefreshResult:
    connection, provider, control = await _verified_source(session, connection_id, workspace_id)
    # The collector atomically compares this identity immediately before it
    # starts collection, closing a provider-switch race between GET and POST.
    return await provider.request_source_refresh(connection.credentials or {}, control.source.provider)
