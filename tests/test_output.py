"""Tests for the JSON shaping helpers in fints_fetch.output."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from mt940.models import Amount as MT940Amount, Balance as MT940Balance, Transaction as MT940Transaction

from fints_fetch.output import (
    amount_entry,
    encode_value,
    fmt_date,
    hbci_balance_entry,
    mt940_balance_entry,
    transaction_entry,
)


# --- encode_value ----------------------------------------------------------


class TestEncodeValue:
    @pytest.mark.parametrize(
        "amount,currency,expected",
        [
            (Decimal("746.70"), "EUR", "74670%2F100%3AEUR"),
            (Decimal("-150.00"), "EUR", "-15000%2F100%3AEUR"),
            (Decimal("0.01"), "EUR", "1%2F100%3AEUR"),
            (Decimal("0"), "EUR", "0%2F100%3AEUR"),
            (Decimal("1000000.00"), "USD", "100000000%2F100%3AUSD"),
        ],
    )
    def test_encodes_to_url_encoded_fraction(self, amount, currency, expected):
        assert encode_value(amount, currency) == expected

    def test_accepts_non_decimal_inputs(self):
        # `transaction_entry` and friends sometimes hand in floats/ints
        # that have already been parsed; the function should normalise.
        assert encode_value(150, "EUR") == "15000%2F100%3AEUR"
        assert encode_value("12.34", "EUR") == "1234%2F100%3AEUR"


# --- fmt_date --------------------------------------------------------------


class TestFmtDate:
    def test_date(self):
        assert fmt_date(date(2025, 11, 1)) == "20251101"

    def test_datetime_strips_time(self):
        assert fmt_date(datetime(2025, 11, 1, 12, 34, 56)) == "20251101"

    def test_none_returns_none(self):
        assert fmt_date(None) is None

    def test_string_passes_through(self):
        # Some callers may already hand in a formatted string.
        assert fmt_date("already-formatted") == "already-formatted"


# --- mt940_balance_entry ---------------------------------------------------


class TestMT940BalanceEntry:
    def test_credit_balance(self):
        bal = MT940Balance("C", "746.70", date(2025, 11, 1), currency="EUR")
        entry = mt940_balance_entry("booked", bal)
        assert entry == {
            "type": "booked",
            "date": "20251101",
            "value": "74670%2F100%3AEUR",
        }

    def test_debit_balance_is_signed_negative(self):
        # mt940 stores `D`/`C` in .status and pre-signs the amount.
        bal = MT940Balance("D", "100.00", date(2025, 11, 1), currency="EUR")
        entry = mt940_balance_entry("booked", bal)
        assert entry is not None
        assert entry["value"] == "-10000%2F100%3AEUR"

    def test_none_balance_returns_none(self):
        assert mt940_balance_entry("booked", None) is None

    def test_missing_amount_returns_none(self):
        bal = SimpleNamespace(amount=None, date=date(2025, 11, 1))
        assert mt940_balance_entry("booked", bal) is None

    def test_missing_currency_returns_none(self):
        bal = SimpleNamespace(
            amount=SimpleNamespace(amount=Decimal("10"), currency=None),
            date=date(2025, 11, 1),
        )
        assert mt940_balance_entry("booked", bal) is None


# --- hbci_balance_entry ----------------------------------------------------


def _make_balance1(
    *, amount=None, currency=None, cd="C", d=date(2025, 11, 1)
):
    """Build a Balance1-shaped object: amount is a bare Decimal."""
    return SimpleNamespace(
        amount=amount,
        currency=currency,
        credit_debit=SimpleNamespace(value=cd),
        date=d,
        time=None,
    )


def _make_balance2(
    *, inner_amount=None, currency=None, cd="C", d=date(2025, 11, 1)
):
    """Build a Balance2-shaped object: amount wraps an Amount1 (inner)."""
    return SimpleNamespace(
        amount=SimpleNamespace(amount=inner_amount, currency=currency),
        credit_debit=SimpleNamespace(value=cd),
        date=d,
        time=None,
    )


class TestHBCIBalanceEntry:
    def test_populated_balance1(self):
        bal = _make_balance1(amount=Decimal("100.50"), currency="EUR", cd="D")
        entry = hbci_balance_entry("noted", bal)
        assert entry == {
            "type": "noted",
            "date": "20251101",
            "value": "-10050%2F100%3AEUR",
        }

    def test_populated_balance2(self):
        bal = _make_balance2(
            inner_amount=Decimal("746.70"), currency="EUR", cd="C"
        )
        entry = hbci_balance_entry("noted", bal)
        assert entry == {
            "type": "noted",
            "date": "20251101",
            "value": "74670%2F100%3AEUR",
        }

    def test_none_balance(self):
        assert hbci_balance_entry("noted", None) is None

    def test_empty_balance1_returns_none(self):
        # The case that originally crashed with 'NoneType to Decimal'.
        assert hbci_balance_entry("noted", _make_balance1()) is None

    def test_empty_balance2_returns_none(self):
        # The case that originally crashed with 'Amount1 to Decimal'.
        assert hbci_balance_entry("noted", _make_balance2()) is None

    def test_amount2_currency_missing_returns_none(self):
        bal = _make_balance2(inner_amount=Decimal("10"), currency=None)
        assert hbci_balance_entry("noted", bal) is None

    def test_time_field_included_when_present(self):
        bal = _make_balance2(
            inner_amount=Decimal("10"), currency="EUR", cd="C"
        )
        bal.time = SimpleNamespace(strftime=lambda fmt: "120000")
        entry = hbci_balance_entry("noted", bal)
        assert entry is not None and entry["time"] == "120000"


# --- amount_entry ----------------------------------------------------------


class TestAmountEntry:
    def test_populated(self):
        a = SimpleNamespace(amount=Decimal("5000.00"), currency="EUR")
        assert amount_entry(a) == "500000%2F100%3AEUR"

    def test_none(self):
        assert amount_entry(None) is None

    def test_missing_inner_amount(self):
        a = SimpleNamespace(amount=None, currency="EUR")
        assert amount_entry(a) is None

    def test_missing_currency(self):
        a = SimpleNamespace(amount=Decimal("10"), currency=None)
        assert amount_entry(a) is None


# --- transaction_entry -----------------------------------------------------


class TestTransactionEntry:
    @pytest.fixture
    def account(self):
        return SimpleNamespace(
            iban="DE24430609671310166000",
            accountnumber="1310166000",
            blz="43060967",
        )

    def _tx(self, **data):
        tx = MT940Transaction(transactions=None, data=data)
        return tx

    def test_minimal_fields(self, account):
        tx = self._tx(
            date=date(2025, 10, 25),
            entry_date=date(2025, 10, 25),
            amount=MT940Amount("150.00", "D", "EUR"),
        )
        entry = transaction_entry(tx, account)
        assert entry["date"] == "20251025"
        assert entry["valutaDate"] == "20251025"
        assert entry["localAccountNumber"] == "1310166000"
        assert entry["value"] == "-15000%2F100%3AEUR"

    def test_full_field_mapping(self, account):
        tx = self._tx(
            date=date(2025, 10, 25),
            entry_date=date(2025, 10, 25),
            amount=MT940Amount("150.00", "D", "EUR"),
            applicant_iban="DE12345678901234567890",
            applicant_bin="GENODEM1GLS",
            applicant_name="ACME GmbH",
            purpose="Payment 1",
            posting_text="UEBERWEISUNG",
            end_to_end_reference="E2E-123",
            customer_reference="CUST-1",
            additional_position_reference="MANDATE-1",
            applicant_creditor_id="DE98ZZZ09999999999",
            purpose_code="OTHR",
            transaction_code="116",
            prima_nota="PN1",
            status="D",
        )
        entry = transaction_entry(tx, account)
        # camelCase remapping is exhaustive
        assert entry["remoteIban"] == "DE12345678901234567890"
        assert entry["remoteBic"] == "GENODEM1GLS"
        assert entry["remoteName"] == "ACME GmbH"
        assert entry["purpose"] == "Payment 1"
        assert entry["postingText"] == "UEBERWEISUNG"
        assert entry["endToEndReference"] == "E2E-123"
        assert entry["customerReference"] == "CUST-1"
        assert entry["mandateReference"] == "MANDATE-1"
        assert entry["creditorId"] == "DE98ZZZ09999999999"
        assert entry["purposeCode"] == "OTHR"
        assert entry["transactionCode"] == "116"
        assert entry["primaNota"] == "PN1"
        assert entry["status"] == "D"

    def test_optional_fields_omitted_when_empty(self, account):
        tx = self._tx(
            date=date(2025, 10, 25),
            entry_date=date(2025, 10, 25),
            amount=MT940Amount("150.00", "D", "EUR"),
            # Empty strings should not produce keys
            purpose="",
            posting_text=None,
        )
        entry = transaction_entry(tx, account)
        assert "purpose" not in entry
        assert "postingText" not in entry

    def test_gvc_iban_fallback(self, account):
        # Some banks put the IBAN in the gvc_applicant_iban slot.
        tx = self._tx(
            date=date(2025, 10, 25),
            entry_date=date(2025, 10, 25),
            amount=MT940Amount("150.00", "D", "EUR"),
            gvc_applicant_iban="DE99500105170123456789",
        )
        entry = transaction_entry(tx, account)
        assert entry["remoteIban"] == "DE99500105170123456789"

    def test_booking_date_falls_back_to_value_date(self, account):
        # When :61: only carries the value date (no entry date marker).
        tx = self._tx(
            date=date(2025, 10, 25),
            amount=MT940Amount("100.00", "C", "EUR"),
        )
        entry = transaction_entry(tx, account)
        assert entry["date"] == "20251025"
        assert entry["valutaDate"] == "20251025"
