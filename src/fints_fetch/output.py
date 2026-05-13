"""JSON shaping helpers.

Turns python-fints / mt940 / camt053 objects into the dict structure
documented in the README. Functions in this module are pure and easy to
unit-test — none of them touch the network or hold state.
"""

from __future__ import annotations

import urllib.parse
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Values are emitted in HBCI4Java-style fractional notation, URL-encoded:
#   Decimal("746.70"), "EUR"  ->  "74670/100:EUR"  ->  "74670%2F100%3AEUR"
# Every currency we surface is handled in minor units, so the denominator
# is always 100. If a non-decimal currency ever shows up (JPY etc.) this
# would need a per-currency exponent table.
_VALUE_DENOM = 100


def encode_value(amount: Decimal | int | float | str, currency: str) -> str:
    """Encode an amount as a URL-encoded HBCI4Java fraction.

    >>> encode_value(Decimal("746.70"), "EUR")
    '74670%2F100%3AEUR'
    >>> encode_value(Decimal("-150.00"), "EUR")
    '-15000%2F100%3AEUR'
    """
    minor = int((Decimal(amount) * _VALUE_DENOM).to_integral_value())
    raw = f"{minor}/{_VALUE_DENOM}:{currency}"
    return urllib.parse.quote(raw, safe="")


def fmt_date(value: str | datetime | date | None) -> str | None:
    """Render a date/datetime as YYYYMMDD; return None for None.

    >>> fmt_date(date(2025, 11, 1))
    '20251101'
    >>> fmt_date(None) is None
    True
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value)


def mt940_balance_entry(kind: str, balance: Any) -> dict[str, Any] | None:
    """Build a balance dict from an ``mt940.models.Balance``.

    Used both for the HKSAL booked balance returned by client.get_balance
    and for every per-statement balance captured during transaction
    parsing. mt940 already applies the sign — `balance.amount.amount` is
    signed (negative for debit), so we don't need to consult
    `balance.status` again. Returns None if any required field is missing.
    """
    if balance is None:
        return None
    amount = getattr(balance, "amount", None)
    if amount is None or amount.amount is None or amount.currency is None:
        return None
    return {
        "type": kind,
        "date": fmt_date(balance.date),
        "value": encode_value(Decimal(amount.amount), amount.currency),
    }


def hbci_balance_entry(kind: str, bal: Any) -> dict[str, Any] | None:
    """Build a balance dict from a python-fints Balance1 or Balance2.

    Used for fields the public API does not surface (notably the noted
    balance). Returns None when the balance is missing or empty —
    optional HBCI fields often come back as a default instance with all
    inner values set to None rather than as None itself.

    The two structures carry the amount differently:

      - ``Balance1``: ``bal.amount`` is a Decimal, ``bal.currency`` is the
        sibling currency.
      - ``Balance2``: ``bal.amount`` is an Amount1 with its own .amount
        + .currency.
    """
    if bal is None:
        return None

    raw_amount = bal.amount
    if raw_amount is None:
        return None

    if hasattr(raw_amount, "amount"):  # Balance2 — amount wrapped in Amount1
        inner = raw_amount.amount
        currency = raw_amount.currency
    else:  # Balance1 — amount is a bare Decimal
        inner = raw_amount
        currency = bal.currency

    if inner is None or currency is None:
        return None

    credit_debit = getattr(bal, "credit_debit", None)
    cd_value = credit_debit.value if credit_debit is not None else "C"
    sign = -1 if cd_value == "D" else 1

    entry: dict[str, Any] = {
        "type": kind,
        "date": fmt_date(bal.date),
        "value": encode_value(sign * Decimal(inner), currency),
    }
    time = getattr(bal, "time", None)
    if time is not None:
        entry["time"] = (
            time.strftime("%H%M%S") if hasattr(time, "strftime") else str(time)
        )
    return entry


def amount_entry(amt: Any) -> str | None:
    """Encode an Amount1 (or None). Returns None if any field is unpopulated."""
    if amt is None:
        return None
    if amt.amount is None or amt.currency is None:
        return None
    return encode_value(Decimal(amt.amount), amt.currency)


def transaction_entry(tx: Any, account: Any) -> dict[str, Any]:
    """Build a single transaction dict from an mt940 Transaction.

    Every key python-fints / mt940 actually populated is included;
    everything else is omitted to keep the output tidy.
    """
    d = tx.data

    # mt940 tag :61: -> `date` = value date, `entry_date` = booking date.
    # HBCI4Java convention is `date` = booking, `valutaDate` = value date.
    booking_date = d.get("entry_date") or d.get("date")
    value_date = d.get("date")

    entry: dict[str, Any] = {
        "date": fmt_date(booking_date),
        "valutaDate": fmt_date(value_date),
        "localAccountNumber": account.accountnumber,
    }

    amount = d.get("amount")
    if amount is not None:
        entry["value"] = encode_value(Decimal(amount.amount), amount.currency)

    # Counterparty
    remote_iban = d.get("applicant_iban") or d.get("gvc_applicant_iban")
    if remote_iban:
        entry["remoteIban"] = remote_iban
    remote_bic = d.get("applicant_bin") or d.get("gvc_applicant_bin")
    if remote_bic:
        entry["remoteBic"] = remote_bic
    if d.get("applicant_name"):
        entry["remoteName"] = d["applicant_name"]
    if d.get("recipient_name"):
        entry["recipientName"] = d["recipient_name"]

    # Purpose / references
    for src_key, out_key in (
        ("purpose", "purpose"),
        ("additional_purpose", "additionalPurpose"),
        ("posting_text", "postingText"),
        ("end_to_end_reference", "endToEndReference"),
        ("customer_reference", "customerReference"),
        ("bank_reference", "bankReference"),
        ("additional_position_reference", "mandateReference"),
        ("applicant_creditor_id", "creditorId"),
        ("purpose_code", "purposeCode"),
        ("transaction_code", "transactionCode"),
        ("prima_nota", "primaNota"),
        ("status", "status"),
    ):
        value = d.get(src_key)
        if value:
            entry[out_key] = value

    return entry
