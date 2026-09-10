"""Source executions are preserved separately from cash and position ledgers."""
import copy
import uuid
from urllib.parse import quote

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.workspace_context import WorkspaceContext
from app.models.workspace import WorkspaceMember
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_execution import AssetExecution
from app.models.asset_transaction import AssetTransaction
from app.models.transaction import Transaction
from app.schemas.investment_feed import InvestmentFeed
from app.services.investment_feed_service import sync_feed
from tests.test_hapoalim_investments import hapoalim_payload
from tests.test_investment_feed import connection


def execution(product_id, number=1):
    source = "natural-key-v1:" + f"{number:064x}"
    return {
        "id": f"{product_id}:execution:{quote(source, safe='')}", "productId": product_id,
        "sourceId": source, "sourceIdKind": "natural_key", "kind": "buy",
        "securityId": "example-security", "isin": "US1234567890", "symbol": "EXAMPLE",
        "name": "Example security", "tradeDate": "2026-08-15", "valueDate": None,
        "settlementDate": "2026-08-17", "cancelDate": None, "cancelled": False,
        "quantity": "3.123456789012", "unitPrice": "8.123456789012",
        "netCashAmount": "-25.00", "currency": "USD", "settlementNetCashAmount": "-90.00",
        "settlementCurrency": "ILS", "sourceTradeType": "Buy", "sourceTransactionType": "Trade",
        "sourcePaymentType": None, "observedAt": "2026-09-08T10:00:00Z",
    }


def feed_with_execution():
    data = hapoalim_payload()
    data["products"][0]["coverage"]["executions"] = "partial"
    data["executions"] = [execution(data["products"][0]["id"])]
    return data


async def seed(session, user, workspace, data):
    row = await connection(session, user, workspace)
    row.institution_name = "Hapoalim Investments"
    row.credentials = {"token": "synthetic-read-token", "source_provider": "hapoalim"}
    await sync_feed(session, row, InvestmentFeed.model_validate(data))
    await session.commit()
    asset = await session.scalar(select(Asset).where(Asset.connection_id == row.id))
    return row, asset


async def test_execution_upserts_preserve_original_currencies_without_financial_side_effects(
    session, test_user, test_workspace,
):
    data = feed_with_execution()
    data["products"][0]["currentValuationId"] = None
    row, asset = await seed(session, test_user, test_workspace, data)
    await sync_feed(session, row, InvestmentFeed.model_validate(data))
    saved = await session.scalar(select(AssetExecution))
    original_id = saved.id
    assert saved.data == InvestmentFeed.model_validate(data).executions[0].model_dump(mode="json")
    assert saved.data["quantity"] == "3.123456789012"
    assert saved.data["currency"] == "USD" and saved.data["settlementCurrency"] == "ILS"
    assert saved.data["netCashAmount"] == "-25.00" and saved.data["settlementNetCashAmount"] == "-90.00"
    assert asset.units is None and asset.average_price is None and asset.realized_gain is None
    assert asset.external_metadata["current_valuation_id"] is None
    assert await session.scalar(select(func.count()).select_from(AssetExecution)) == 1
    assert await session.scalar(select(func.count()).select_from(AssetTransaction)) == 0
    assert await session.scalar(select(func.count()).select_from(Account)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0

    corrected = copy.deepcopy(data)
    corrected["generatedAt"] = "2026-09-09T10:00:00Z"
    corrected["executions"][0].update(cancelled=True, cancelDate="2026-09-09", observedAt="2026-09-09T10:00:00Z")
    await sync_feed(session, row, InvestmentFeed.model_validate(corrected))
    assert saved.id == original_id and saved.data["cancelled"] is True
    # A newer cache response with an older row observation cannot undo it.
    older_row = copy.deepcopy(data)
    older_row["generatedAt"] = "2026-09-10T10:00:00Z"
    await sync_feed(session, row, InvestmentFeed.model_validate(older_row))
    assert saved.data["cancelled"] is True
    empty = copy.deepcopy(older_row)
    empty["generatedAt"] = "2026-09-11T10:00:00Z"
    empty.update(products=[], valuations=[], activities=[], tracks=[], executions=[])
    empty["source"].update(status="error", inventoryComplete=False)
    await sync_feed(session, row, InvestmentFeed.model_validate(empty))
    assert await session.scalar(select(func.count()).select_from(AssetExecution)) == 1


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(id="unrelated-execution"),
    lambda row: row.update(productId="unknown-product"),
    lambda row: row.update(sourceId="invented-sequence"),
    lambda row: row.update(sourceIdKind="provider_id"),
    lambda row: row.update(quantity="-1"),
    lambda row: row.update(quantity="1e10"),
    lambda row: row.update(quantity="1.1234567890123"),
    lambda row: row.update(unitPrice="01.00"),
    lambda row: row.update(netCashAmount="1.1234"),
    lambda row: row.update(observedAt="2026-09-08T10:00:00"),
])
def test_execution_contract_rejects_ambiguous_or_lossy_input(mutation):
    data = feed_with_execution()
    mutation(data["executions"][0])
    with pytest.raises(ValidationError):
        InvestmentFeed.model_validate(data)


def test_duplicate_execution_identity_is_rejected_and_missing_array_is_empty():
    data = feed_with_execution()
    data["executions"].append(copy.deepcopy(data["executions"][0]))
    with pytest.raises(ValidationError, match="Duplicate"):
        InvestmentFeed.model_validate(data)
    assert InvestmentFeed.model_validate(hapoalim_payload()).executions == []


async def test_execution_account_api_is_paginated_scoped_and_exported(
    session, test_user, test_workspace, client, auth_headers,
):
    data = feed_with_execution()
    second = execution(data["products"][0]["id"], 2)
    second.update(kind="dividend", tradeDate="2025-06-01", quantity=None, unitPrice=None,
                  netCashAmount="5.00", settlementNetCashAmount=None)
    data["executions"].append(second)
    _, asset = await seed(session, test_user, test_workspace, data)
    endpoint = f"/api/investment-accounts/{asset.id}/executions"
    response = await client.get(endpoint + "?limit=1&year=2025&kind=dividend", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1 and body["available_years"] == [2026, 2025]
    assert body["available_kinds"] == ["buy", "dividend"]
    assert body["items"][0]["netCashAmount"] == "5.00" and body["items"][0]["currency"] == "USD"
    assert body["items"][0]["quantity"] is None
    assert (await client.get(endpoint + "?limit=101", headers=auth_headers)).status_code == 422
    assert (await client.get(f"/api/investment-accounts/{uuid.uuid4()}/executions", headers=auth_headers)).status_code == 404

    # A row in another workspace cannot be exposed, even via a known asset ID.
    original_workspace = asset.workspace_id
    asset.workspace_id = uuid.uuid4()
    await session.commit()
    assert (await client.get(endpoint, headers=auth_headers)).status_code == 404
    asset.workspace_id = original_workspace
    await session.commit()
    from app.api.export import _collect
    member = await session.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == test_workspace.id, WorkspaceMember.user_id == test_user.id,
    ))
    assert member is not None
    exported = await _collect(WorkspaceContext(test_workspace, member, test_user), session)
    executions = exported["asset_executions.json"]
    assert isinstance(executions, list) and len(executions) == 2
    assert executions[0]["data"]["quantity"] in (None, "3.123456789012")


def test_archival_source_amount_cannot_be_replaced_by_rounded_value():
    data = hapoalim_payload()
    data["valuations"][0].update(amount="123.46", provenance={
        "origin": "sure_archive", "sourceEntryId": str(uuid.uuid4()), "sourceAccountId": str(uuid.uuid4()),
        "sourceSha256": "a" * 64, "archiveObservedAt": "2026-09-10T10:00:00Z",
        "observationBasis": "archive_read", "bankObservationVerified": False, "sourceAmount": "123.4567",
    })
    with pytest.raises(ValidationError, match="preserve its source amount"):
        InvestmentFeed.model_validate(data)
