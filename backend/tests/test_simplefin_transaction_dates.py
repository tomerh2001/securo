"""Source dates remain distinct from billing dates through imports and edits."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.transaction import Transaction
from app.providers.base import AccountData, ConnectionData
from app.providers.simplefin import SimpleFinProvider
from app.schemas.account import AccountRead
from app.services.account_service import serialize_account
from app.services.connection_service import handle_oauth_callback, sync_connection
from app.services.credit_card_service import apply_effective_date
from tests.test_connection_service import _make_connection


def source_transaction(**extra):
    raw = {
        "id": "stable-source-id", "amount": "-123.45", "currency": "ILS",
        "description": "Original merchant", "posted": 1790899200,
        "extra": {"transaction_date": "2026-09-06", "transaction_date_kind": "purchase",
                  "charge_date": "2026-10-02", **extra},
    }
    transaction = SimpleFinProvider._build_transaction(raw, "description")
    assert transaction is not None
    return transaction


@pytest.mark.parametrize("kind", ["purchase", "installment_occurrence", "archive_purchase_or_occurrence"])
def test_explicit_dates_keep_stable_identity_and_source(kind):
    transaction = source_transaction(transaction_date_kind=kind)
    assert transaction.external_id == "stable-source-id"
    assert transaction.description == "Original merchant"
    assert transaction.amount == Decimal("123.45")
    assert transaction.date == date(2026, 9, 6)
    assert transaction.provider_bill_date == date(2026, 10, 2)
    assert transaction.installment_purchase_date is None
    assert transaction.raw_data["extra"]["transaction_date_kind"] == kind


@pytest.mark.parametrize("extra", [
    {"transaction_date": "2026-09"}, {"transaction_date": "2026-02-30"},
    {"transaction_date": "20260906"}, {"transaction_date_kind": "guessed"},
    {"transaction_date_kind": ["purchase"]},
])
def test_invalid_occurrence_metadata_uses_protocol_date(extra):
    transaction = source_transaction(**extra)
    assert transaction.date == date(2026, 10, 2)
    assert transaction.provider_bill_date is None
    assert "_securo_provider_dates" not in transaction.raw_data


def test_invalid_bill_date_does_not_discard_purchase():
    transaction = source_transaction(charge_date="2026-10")
    assert transaction.date == date(2026, 9, 6)
    assert transaction.provider_bill_date is None


def test_raw_marker_cannot_override_adapter_validation():
    raw = {"id": "x", "amount": "1", "posted": 1790899200,
           "_securo_provider_dates": {"provider": "simplefin", "bill_date": "2030-01-01"}}
    transaction = SimpleFinProvider._build_transaction(raw, "none")
    assert transaction is not None and transaction.raw_data is not None
    assert "_securo_provider_dates" not in transaction.raw_data


@pytest.mark.parametrize("source,override,linked,expected", [
    ("sync", None, None, date(2026, 10, 2)),
    ("sync", date(2026, 11, 3), None, date(2026, 11, 3)),
    ("sync", None, date(2026, 10, 4), date(2026, 10, 4)),
    ("manual", None, None, date(2026, 9, 6)),
])
def test_recomputation_preserves_source_billing_and_override_precedence(source, override, linked, expected):
    incoming = source_transaction()
    tx = SimpleNamespace(date=incoming.date, source=source, raw_data=incoming.raw_data,
                         effective_bill_date=override)
    apply_effective_date(tx, SimpleNamespace(type="credit_card"), bill_due_date=linked)
    assert tx.effective_date == expected
    apply_effective_date(tx, SimpleNamespace(type="checking"))
    assert tx.effective_date == (override or incoming.date)


@pytest.mark.parametrize("value,expected", [
    ("next_statement_debit", "next_statement_debit"), ("balance", "balance"),
    ("credit_card", None), (None, None), (["balance"], None),
])
def test_balance_semantics_require_explicit_supported_metadata(value, expected):
    _, accounts = SimpleFinProvider._parse_accounts({"accounts": [
        {"id": "a", "name": "CAL", "balance": "-123", "currency": "ILS",
         "extra": {"balance_semantics": value}}
    ]})
    assert accounts[0].balance_semantics == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True])
async def test_import_and_resync_preserve_dates_descriptions_and_manual_overrides(
    session, test_user, test_workspace, initial,
):
    incoming = source_transaction()
    account_data = AccountData(external_id="cc-source", name="Card", type="credit_card",
                               balance=Decimal("123.45"), currency="ILS",
                               balance_semantics="next_statement_debit")
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "fake"})
    provider.get_accounts = AsyncMock(return_value=[account_data])
    provider.get_transactions = AsyncMock(return_value=[incoming])
    provider.get_bills = AsyncMock(return_value=[])
    provider.get_holdings = AsyncMock(return_value=[])
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="date-test", institution_name="Card", credentials={"token": "fake"}, accounts=[account_data],
    ))
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        if initial:
            conn = await handle_oauth_callback(session, test_workspace.id, test_user.id, "code", "test")
        else:
            conn = await _make_connection(session, test_user.id)
            await sync_connection(session, conn.id, test_workspace.id, test_user.id)
        tx = (await session.execute(select(Transaction).where(Transaction.external_id == incoming.external_id))).scalar_one()
        account = await session.get(Account, tx.account_id)
        assert tx.date == date(2026, 9, 6) and tx.effective_date == date(2026, 10, 2)
        assert tx.effective_bill_date is None
        assert account is not None
        assert account.balance_semantics == "next_statement_debit"
        dto = AccountRead.model_validate(serialize_account(account, Decimal("0"), None))
        assert dto.balance_semantics == "next_statement_debit"
        tx.description = "Translated merchant"
        tx.notes = "Personal note"
        tx.effective_bill_date = date(2026, 11, 3)
        apply_effective_date(tx, account)
        await session.commit()
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
        assert tx.description == "Translated merchant" and tx.notes == "Personal note"
        assert tx.effective_date == date(2026, 11, 3)
        tx.date = date(2026, 10, 2)
        tx.effective_bill_date = None
        tx.effective_date = tx.date
        await session.commit()
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
        assert tx.date == tx.effective_date == date(2026, 10, 2)
        assert len((await session.execute(select(Transaction).where(Transaction.external_id == incoming.external_id))).scalars().all()) == 1


@pytest.mark.parametrize("origin", ["actual_archive", "sure_archive"])
def test_archive_identity_normalized_without_copying_source_record(origin):
    provenance = {"origin": origin, "source_record_id": "c7ea7044-bf53-4538-8566-b2e640a73467",
                  "unneeded_source_record": {"private": "data"}}
    transaction = source_transaction(source_provenance=provenance)
    assert transaction.raw_data is not None
    assert transaction.raw_data["source_provenance"] == {
        "origin": origin, "source_record_id": provenance["source_record_id"],
    }
    assert transaction.external_id == "stable-source-id"
    assert transaction.description == "Original merchant"


@pytest.mark.parametrize("value", [
    None, [], {"origin": "guessed", "source_record_id": "c7ea7044-bf53-4538-8566-b2e640a73467"},
    {"origin": "actual_archive", "source_record_id": "merchant-id"},
    {"origin": "sure_archive", "source_record_id": 123},
])
def test_archive_origin_requires_recognized_source_and_record_uuid(value):
    transaction = source_transaction(source_provenance=value)
    assert transaction.raw_data is not None and "source_provenance" not in transaction.raw_data
