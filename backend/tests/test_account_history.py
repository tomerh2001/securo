"""Coverage is evidence about stored history, not an invented completeness guarantee."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_activity import AssetActivity
from app.models.asset_execution import AssetExecution
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from tests.test_investment_accounts import provider_payload, seed_account


async def test_bank_counts_ranges_opening_and_workspace_isolation(
    session, test_user, test_workspace, test_account, client, auth_headers,
):
    other = Workspace(name="Other", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    for when, source, workspace_id in [
        (date(2023, 12, 31), "opening_balance", test_workspace.id),
        (date(2024, 1, 8), "sync", test_workspace.id),
        (date(2024, 1, 9), "sync", test_workspace.id),
        (date(2024, 3, 2), "sync", test_workspace.id),
        # Even a corrupt cross-workspace child must not affect this endpoint.
        (date(1999, 1, 1), "sync", other.id),
    ]:
        session.add(Transaction(
            user_id=test_user.id, workspace_id=workspace_id, account_id=test_account.id,
            amount=Decimal("1"), type="credit", currency="BRL", date=when,
            description="Example", source=source,
        ))
    await session.commit()
    response = await client.get(f"/api/accounts/{test_account.id}/history-coverage", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["account_id"] == str(test_account.id) and data["account_kind"] == "bank"
    assert data["opening_balance_date"] == "2023-12-31"
    assert data["balance_as_of"] is None  # A connection's sync timestamp is not a bank date.
    assert data["streams"] == [{
        "kind": "transactions", "count": 3, "first_date": "2024-01-08", "last_date": "2024-03-02",
        "availability": "partial", "contains_archive": False,
        "monthly_counts": [{"month": "2024-01", "count": 2}, {"month": "2024-03", "count": 1}],
    }]
    assert "bank_history_completeness_unknown" in data["note_codes"]


async def test_no_bank_rows_distinguishes_manual_empty_from_unavailable_source(
    session, test_user, test_workspace, test_account, client, auth_headers,
):
    manual = Account(user_id=test_user.id, workspace_id=test_workspace.id, name="Wallet",
                     type="checking", balance=Decimal("0"), currency="BRL")
    session.add(manual)
    await session.commit()
    for account, availability in [(manual, "empty"), (test_account, "unavailable")]:
        data = (await client.get(f"/api/accounts/{account.id}/history-coverage", headers=auth_headers)).json()
        assert data["streams"][0]["availability"] == availability
        assert data["streams"][0]["count"] == 0
        assert data["streams"][0]["first_date"] is None
        assert data["streams"][0]["monthly_counts"] == []


async def test_bank_archive_flag_requires_explicit_provenance(
    session, test_user, test_workspace, test_account, client, auth_headers,
):
    row = Transaction(user_id=test_user.id, workspace_id=test_workspace.id,
                      account_id=test_account.id, amount=Decimal("5"), type="debit",
                      currency="BRL", date=date(2024, 1, 1), description="Imported",
                      source="csv", raw_data={"archive": True})
    session.add(row)
    await session.commit()
    url = f"/api/accounts/{test_account.id}/history-coverage"
    assert not (await client.get(url, headers=auth_headers)).json()["streams"][0]["contains_archive"]
    row.raw_data = {"source_provenance": {"origin": "actual_archive"}}
    await session.commit()
    data = (await client.get(url, headers=auth_headers)).json()
    assert data["streams"][0]["contains_archive"]
    assert "contains_archive_history" in data["note_codes"]


async def test_investment_ranges_preserve_months_and_exclude_cleared_activities(
    session, test_user, test_workspace, client, auth_headers,
):
    _, asset = await seed_account(session, test_user, test_workspace)
    session.add_all([
        AssetActivity(asset_id=asset.id, workspace_id=test_workspace.id,
                      external_id="earlier", source_id="earlier", kind="management_fee",
                      date="2025-12-04", date_kind="booking", amount=Decimal("-2"),
                      currency="ILS", description="Fee", observed_at=datetime.now(timezone.utc)),
        AssetActivity(asset_id=asset.id, workspace_id=test_workspace.id,
                      external_id="cleared", source_id="cleared", kind="other",
                      date="2020-01", date_kind="contribution_month", amount=Decimal("0"),
                      currency="ILS", description="Cleared source correction",
                      observed_at=datetime.now(timezone.utc)),
    ])
    await session.commit()
    data = (await client.get(f"/api/investment-accounts/{asset.id}/history-coverage", headers=auth_headers)).json()
    assert data["account_kind"] == "investment"
    assert data["balance_as_of"] == "2026-08-31"
    assert data["opening_balance_date"] is None
    streams = {row["kind"]: row for row in data["streams"]}
    assert streams["valuations"]["count"] == 1
    assert streams["activities"]["count"] == 2
    assert streams["activities"]["first_date"] == "2025-12-04"
    assert streams["activities"]["last_date"] == "2026-08"
    assert streams["activities"]["monthly_counts"] == [
        {"month": "2025-12", "count": 1}, {"month": "2026-08", "count": 1},
    ]
    assert streams["executions"]["availability"] == "unavailable"
    assert streams["executions"]["count"] == 0
    assert "activity_month_precision" in data["note_codes"]


@pytest.mark.parametrize("coverage,expected", [
    ("complete", "empty"), ("partial", "partial"), ("unavailable", "unavailable"),
])
async def test_investment_empty_stream_honors_source_coverage(
    session, test_user, test_workspace, client, auth_headers, coverage, expected,
):
    payload = provider_payload()
    payload["products"][0]["coverage"]["activities"] = coverage
    payload["activities"] = []
    _, asset = await seed_account(session, test_user, test_workspace, payload)
    data = (await client.get(f"/api/investment-accounts/{asset.id}/history-coverage", headers=auth_headers)).json()
    activity = next(stream for stream in data["streams"] if stream["kind"] == "activities")
    assert activity["availability"] == expected and activity["count"] == 0
    assert activity["first_date"] is activity["last_date"] is None


async def test_unverified_valuation_date_is_not_replaced_by_observation_date(
    session, test_user, test_workspace, client, auth_headers,
):
    payload = provider_payload()
    payload["products"][0]["coverage"]["valuations"] = "complete"
    payload["valuations"][0]["asOf"] = None
    _, asset = await seed_account(session, test_user, test_workspace, payload)
    data = (await client.get(f"/api/investment-accounts/{asset.id}/history-coverage", headers=auth_headers)).json()
    stream = next(row for row in data["streams"] if row["kind"] == "valuations")
    assert stream["count"] == 1 and stream["availability"] == "partial"
    assert stream["first_date"] is stream["last_date"] is None
    assert stream["monthly_counts"] == []
    assert data["balance_as_of"] is None
    assert "valuation_dates_unverified" in data["note_codes"]


async def test_archive_and_foreign_children_do_not_invent_bank_observations(
    session, test_user, test_workspace, client, auth_headers,
):
    _, asset = await seed_account(session, test_user, test_workspace)
    value = await session.scalar(select(AssetValue).where(AssetValue.asset_id == asset.id))
    value.source_provenance = {"origin": "sure_archive", "bankObservationVerified": False}
    other = Workspace(name="Other", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    session.add_all([
        AssetValue(asset_id=asset.id, workspace_id=other.id, amount=Decimal("1"),
                   date=date(1990, 1, 1), source="manual"),
        AssetExecution(asset_id=asset.id, workspace_id=other.id, external_id="foreign",
                       trade_date=date(1990, 1, 1), kind="buy", data={},
                       observed_at=datetime.now(timezone.utc)),
        AssetActivity(asset_id=asset.id, workspace_id=other.id, external_id="foreign",
                      source_id="foreign", kind="other", date="1990-01", date_kind="contribution_month",
                      amount=Decimal("5"), currency="ILS", description="Other workspace",
                      observed_at=datetime.now(timezone.utc)),
    ])
    await session.commit()
    data = (await client.get(f"/api/investment-accounts/{asset.id}/history-coverage", headers=auth_headers)).json()
    assert data["balance_as_of"] is None
    streams = {row["kind"]: row for row in data["streams"]}
    assert streams["valuations"]["contains_archive"]
    assert streams["valuations"]["count"] == 1
    assert streams["valuations"]["first_date"] == "2026-08-31"
    assert streams["activities"]["count"] == 1 and streams["executions"]["count"] == 0


async def test_history_requires_auth_and_source_backed_workspace_account(
    session, test_user, test_workspace, test_account, client, auth_headers,
):
    other = Workspace(name="Other", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    foreign_bank = Account(user_id=test_user.id, workspace_id=other.id, name="Elsewhere",
                           type="checking", currency="BRL", balance=Decimal("0"))
    manual = Asset(user_id=test_user.id, workspace_id=test_workspace.id, name="Manual asset",
                   type="investment", source="manual", currency="ILS")
    foreign_asset = Asset(user_id=test_user.id, workspace_id=other.id, name="Foreign asset",
                          type="investment", source="investment_feed", currency="ILS",
                          external_metadata={"investment_details": {"product_kind": "investment"}})
    session.add_all([foreign_bank, manual, foreign_asset])
    await session.commit()
    for prefix, identity in [
        ("accounts", foreign_bank.id), ("accounts", uuid.uuid4()),
        ("investment-accounts", manual.id), ("investment-accounts", foreign_asset.id),
        ("investment-accounts", uuid.uuid4()),
    ]:
        url = f"/api/{prefix}/{identity}/history-coverage"
        assert (await client.get(url, headers=auth_headers)).status_code == 404
        assert (await client.get(url)).status_code == 401


async def test_viewers_can_read_history(client, viewer_auth_headers, test_account):
    response = await client.get(f"/api/accounts/{test_account.id}/history-coverage", headers=viewer_auth_headers)
    assert response.status_code == 200
