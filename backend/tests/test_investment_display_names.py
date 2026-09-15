"""User labels stay separate from collector identity and financial history."""
from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import select

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_execution import AssetExecution
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.investment_feed import InvestmentFeed
from app.services import asset_service, report_service
from app.services.investment_feed_service import sync_feed
from tests.test_investment_accounts import provider_payload, seed_account


async def financial_snapshot(session):
    result = []
    for model in (Asset, AssetValue, AssetActivity, AssetExecution, Account, Transaction):
        columns = [column for column in model.__table__.columns
                   if model is not Asset or column.name not in {"name", "display_name"}]
        result.append((await session.execute(select(*columns).order_by(model.id))).all())
    return result


async def test_investment_alias_survives_refresh_and_labels_every_view(
    session, test_user, test_workspace, client, auth_headers,
):
    conn, asset = await seed_account(session, test_user, test_workspace)
    asset_id = asset.id
    alias = "Clal Pension · 5678"
    before = await financial_snapshot(session)
    value_before = await asset_service.get_asset_values_at(
        session, test_workspace.id, by_workspace=True,
    )
    response = await client.patch(f"/api/assets/{asset_id}", headers=auth_headers,
                                  json={"display_name": f"  {alias}  "})
    assert response.status_code == 200
    assert response.json()["name"] == response.json()["display_name"] == alias
    assert response.json()["current_value"] == 10000

    # A later provider rename must update the source name without replacing the alias.
    data = provider_payload()
    source_name = "Updated provider insurance plan"
    data["products"][0]["name"] = source_name
    await sync_feed(session, conn, InvestmentFeed.model_validate(data))
    await session.commit()
    await session.refresh(asset)
    assert asset.name == source_name and asset.display_name == alias
    assert await financial_snapshot(session) == before
    assert await asset_service.get_asset_values_at(
        session, test_workspace.id, by_workspace=True,
    ) == value_before

    for path in (f"/api/assets/{asset_id}", f"/api/investment-accounts/{asset_id}"):
        read = await client.get(path, headers=auth_headers)
        assert read.status_code == 200
        assert read.json()["id"] == str(asset_id)
        assert read.json()["name"] == read.json()["display_name"] == alias
    for path in ("/api/assets", "/api/investment-accounts"):
        read = await client.get(path, headers=auth_headers)
        assert read.status_code == 200
        assert read.json()[0]["name"] == alias
    activities = await client.get("/api/assets/activities", headers=auth_headers)
    assert activities.json()[0]["asset_name"] == alias
    activities = await client.get(f"/api/investment-accounts/{asset_id}/activities",
                                  headers=auth_headers)
    assert activities.json()["items"][0]["asset_name"] == alias
    trend = await client.get("/api/assets/portfolio-trend", headers=auth_headers)
    assert trend.json()["assets"][0]["name"] == alias
    report = await report_service._net_worth_at(
        session, test_workspace.id, date(2026, 8, 31), "ILS",
    )
    assert report.composition[0].label == alias
    assert report.composition[0].value == 10000
    for query in ("Clal Pension", "Updated provider"):
        search = await client.get("/api/search", headers=auth_headers, params={"q": query})
        hits = [row for row in search.json()["results"] if row["type"] == "asset"]
        assert [(row["id"], row["label"]) for row in hits] == [(str(asset_id), alias)]

    # Both explicit null and a blank label restore the current provider name.
    for reset in (None, " \t "):
        await client.patch(f"/api/assets/{asset_id}", headers=auth_headers,
                           json={"display_name": alias})
        read = await client.patch(f"/api/assets/{asset_id}", headers=auth_headers,
                                  json={"display_name": reset})
        assert read.status_code == 200
        assert read.json()["display_name"] is None
        assert read.json()["name"] == source_name
    assert await financial_snapshot(session) == before


async def test_alias_validation_and_permissions_preserve_source_finances(
    session, test_user, test_workspace, client, auth_headers, viewer_auth_headers,
):
    _, asset = await seed_account(session, test_user, test_workspace)
    path = f"/api/assets/{asset.id}"
    before = await financial_snapshot(session)
    for update in ({"name": "Wrong source"}, {"currency": "USD"}, {"purchase_price": 123},
                   {"display_name": "Rejected with financial edit", "units": 5}):
        response = await client.patch(path, headers=auth_headers, json=update)
        assert response.status_code == 400
        assert "read-only" in response.json()["detail"]
    for value in ("x" * 256, 123):
        response = await client.patch(path, headers=auth_headers, json={"display_name": value})
        assert response.status_code == 422
    response = await client.patch(path, headers=viewer_auth_headers,
                                  json={"display_name": "Not writable"})
    assert response.status_code == 403
    response = await client.patch(path, headers={**auth_headers, "X-Workspace-Id": str(uuid.uuid4())},
                                  json={"display_name": "Wrong workspace"})
    assert response.status_code in (403, 404)
    await session.refresh(asset)
    assert asset.display_name is None
    assert asset.name == "Example pension"
    assert await financial_snapshot(session) == before


async def test_goal_and_asset_trade_labels_use_alias(session, test_user, test_workspace, client, auth_headers):
    # Manual holding: collector products must never receive invented buy/sell entries.
    asset = Asset(user_id=test_user.id, workspace_id=test_workspace.id, name="Original holding",
                  type="investment", currency="ILS", purchase_price=Decimal("100"))
    session.add(asset)
    await session.flush()
    session.add(AssetTransaction(asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
                                quantity=Decimal("1"), price=Decimal("100"), date=date(2026, 8, 1)))
    session.add(Goal(user_id=test_user.id, workspace_id=test_workspace.id, name="Savings goal",
                     target_amount=Decimal("500"), currency="ILS", tracking_type="asset", asset_id=asset.id))
    await session.commit()
    response = await client.patch(f"/api/assets/{asset.id}", headers=auth_headers,
                                  json={"display_name": "My holding"})
    assert response.status_code == 200
    goals = await client.get("/api/goals", headers=auth_headers)
    assert goals.status_code == 200
    assert goals.json()[0]["asset_name"] == "My holding"
    trades = await client.get("/api/assets/transactions", headers=auth_headers)
    assert trades.status_code == 200
    assert trades.json()[0]["asset_name"] == "My holding"
    assert trades.json()[0]["quantity"] == 1 and trades.json()[0]["price"] == 100
