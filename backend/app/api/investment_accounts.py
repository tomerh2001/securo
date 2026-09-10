import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.investment_account import (
    InvestmentAccountActivitiesRead, InvestmentAccountExecutionsRead, InvestmentAccountRead,
)
from app.schemas.investment_feed import ActivityKind, ExecutionKind
from app.services import investment_account_service

router = APIRouter(prefix="/api/investment-accounts", tags=["investment accounts"])


@router.get("", response_model=list[InvestmentAccountRead])
async def list_accounts(
    connection_id: uuid.UUID | None = None,
    include_archived: bool = False,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await investment_account_service.list_accounts(
        session, ctx.workspace.id, ctx.user.primary_currency,
        connection_id=connection_id, include_archived=include_archived,
    )


@router.get("/{asset_id}", response_model=InvestmentAccountRead)
async def get_account(
    asset_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await investment_account_service.get_account(
        session, ctx.workspace.id, asset_id, ctx.user.primary_currency,
    )


@router.get("/{asset_id}/activities", response_model=InvestmentAccountActivitiesRead)
async def list_activities(
    asset_id: uuid.UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    kind: ActivityKind | None = None,
    year: int | None = Query(None, ge=1, le=9999),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await investment_account_service.get_activities(
        session, ctx.workspace.id, asset_id, page=page, limit=limit, kind=kind, year=year,
    )


@router.get("/{asset_id}/executions", response_model=InvestmentAccountExecutionsRead)
async def list_executions(
    asset_id: uuid.UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    kind: ExecutionKind | None = None,
    year: int | None = Query(None, ge=1, le=9999),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await investment_account_service.get_executions(
        session, ctx.workspace.id, asset_id, page=page, limit=limit, kind=kind, year=year,
    )
