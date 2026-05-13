"""Per-statement balance capture.

Banks return transaction data in one of two formats:

1. **MT940 via HKKAZ** — older format. python-fints parses this through
   ``fints.utils.mt940_to_array``, which builds a flat transaction list
   and discards every ``:60F:``/``:60M:``/``:62F:``/``:62M:``/``:64:``
   /``:65:`` balance the bank emitted along the way.

2. **camt053 via HKCAZ** — newer ISO 20022 XML format. python-fints
   parses this through ``fints.camt_parser.camt053_to_dict``, which
   extracts only ``<Ntry>`` (transaction) elements and ignores every
   ``<Bal>`` element.

Most modern German banks have migrated to camt053; some still serve
MT940; python-fints prefers MT940 when both are advertised. To stay
robust we patch BOTH parsers when ``capture_balances()`` is active and
forward whatever balances either path produces into a single capture
list.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import mt940
import mt940.models

from .output import encode_value, fmt_date, mt940_balance_entry

# mt940 tag slugs we install post-processors on. These are the eight
# balance-bearing tags in the SWIFT MT940 spec.
_BALANCE_SLUGS: tuple[str, ...] = (
    "opening_balance",
    "final_opening_balance",
    "intermediate_opening_balance",
    "closing_balance",
    "final_closing_balance",
    "intermediate_closing_balance",
    "available_balance",
    "forward_available_balance",
)

# Maps the mt940 tag slug to a stable camelCase identifier used in our
# JSON output. The F/M distinction is preserved so callers can tell
# range-wide balances (final*) from per-statement boundaries
# (intermediate*).
_SLUG_TO_TYPE: dict[str, str] = {
    "opening_balance": "opening",
    "final_opening_balance": "finalOpening",
    "intermediate_opening_balance": "intermediateOpening",
    "closing_balance": "closing",
    "final_closing_balance": "finalClosing",
    "intermediate_closing_balance": "intermediateClosing",
    "available_balance": "available",
    "forward_available_balance": "forwardAvailable",
}

# Maps ISO 20022 camt053 balance type codes to our JSON identifiers.
# Spec: ExternalBalanceType1Code from urn:iso:std:iso:20022:tech:xsd:camt.053
#   OPBD - Opening Booked (statement period start)
#   PRCD - Previously Closed Booked (closing balance of the prior period)
#   CLBD - Closing Booked (statement period end)
#   CLAV - Closing Available
#   ITBD - Interim Booked (per-day or intra-period checkpoint)
#   ITAV - Interim Available
#   FWAV - Forward Available (future-dated)
#   INFO - Information only
_CAMT053_TYPE_MAP: dict[str, str] = {
    "OPBD": "opening",
    "PRCD": "previouslyClosed",
    "CLBD": "closing",
    "CLAV": "closingAvailable",
    "ITBD": "interim",
    "ITAV": "interimAvailable",
    "FWAV": "forwardAvailable",
    "INFO": "info",
}


def _local_tag(tag: str) -> str:
    """Strip an XML namespace, returning just the local tag name."""
    idx = tag.find("}")
    return tag[idx + 1 :] if idx >= 0 else tag


def parse_camt053_balances(
    xml_bytes: bytes,
) -> list[tuple[str, dict[str, Any]]]:
    """Extract every ``<Bal>`` element from a camt053 XML payload.

    Returns a list of ``(type_code, info)`` tuples in document order,
    where ``info`` carries the fields needed downstream: a ``date``
    (``datetime.date`` or ``None``), a signed ``value`` (``Decimal``),
    and a ``currency`` string. Malformed/incomplete ``<Bal>`` elements
    are skipped silently — this function never raises on bad input.
    """
    from lxml import etree

    root = etree.fromstring(xml_bytes)
    out: list[tuple[str, dict[str, Any]]] = []

    for bal in root.iter():
        if not hasattr(bal.tag, "find") or _local_tag(bal.tag) != "Bal":
            continue

        code: str | None = None
        amount_text: str | None = None
        currency: str | None = None
        cdt_dbt: str | None = None
        date_text: str | None = None

        for child in bal.iter():
            name = _local_tag(child.tag)
            if name == "Cd" and code is None:
                code = (child.text or "").strip()
            elif name == "Prtry" and code is None:
                code = (child.text or "").strip()
            elif name == "Amt":
                amount_text = (child.text or "").strip()
                currency = child.get("Ccy")
            elif name == "CdtDbtInd":
                cdt_dbt = (child.text or "").strip()
            elif name in ("Dt", "DtTm") and not date_text:
                # The balance date lives in <Dt><Dt>YYYY-MM-DD</Dt></Dt>;
                # the outer wrapper has no direct text content, so we
                # keep looking until we see a non-empty value. ISO date
                # and datetime forms are both accepted.
                text = (child.text or "").strip()
                if text:
                    date_text = text

        if not (code and amount_text and currency and cdt_dbt and date_text):
            continue

        sign = -1 if cdt_dbt == "DBIT" else 1
        try:
            value = sign * Decimal(amount_text)
        except (ArithmeticError, ValueError):
            continue

        bal_date: date | None
        try:
            bal_date = datetime.fromisoformat(date_text[:10]).date()
        except ValueError:
            bal_date = None

        out.append(
            (code, {"date": bal_date, "value": value, "currency": currency})
        )

    return out


@contextlib.contextmanager
def capture_balances() -> Iterator[list[dict[str, Any]]]:
    """Context manager that captures every statement balance from either format.

    Patches both ``fints.client.mt940_to_array`` (for HKKAZ/MT940 banks)
    and ``fints.client.camt053_to_dict`` (for HKCAZ/camt053 banks) for
    the duration of the ``with`` block, and yields a list that fills up
    with fully-shaped balance entries (``{"type", "date", "value"}``
    dicts) in document order. Both patches are restored on exit, even
    if the block raises.

    Usage::

        with capture_balances() as captured:
            txs = client.get_transactions(account, start_date=..., end_date=...)
        # captured is now a list of balance dicts
    """
    captured: list[dict[str, Any]] = []

    # --- MT940 side -------------------------------------------------------
    def _mt940_post_process(transactions, tag, tag_dict, result):  # noqa: ARG001
        for slug, balance in result.items():
            entry = mt940_balance_entry(_SLUG_TO_TYPE.get(slug, slug), balance)
            if entry is not None:
                captured.append(entry)
        return result

    class _CapturingTransactions(mt940.models.Transactions):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            # NOTE: mt940's __init__ does `self.processors = DEFAULT_PROCESSORS.copy()`,
            # which is shallow — the inner lists are still shared with the
            # class attribute. So we must REPLACE each list, not append to
            # it, or we'd mutate global state and leak processors across
            # instances and runs.
            for slug in _BALANCE_SLUGS:
                key = f"post_{slug}"
                self.processors[key] = list(self.processors.get(key, [])) + [
                    _mt940_post_process
                ]

    def _patched_mt940_to_array(data: str) -> list[Any]:
        data = data.replace("@@", "\r\n").replace("-0000", "+0000")
        return _CapturingTransactions().parse(data)

    # --- camt053 side -----------------------------------------------------
    import fints.client

    original_mt940 = fints.client.mt940_to_array
    original_camt053 = fints.client.camt053_to_dict

    def _patched_camt053_to_dict(xml_data: bytes, translate: bool = True):
        # Extract balances first so they land in the captured list in
        # roughly statement order. Failures here must not break the
        # transaction path that follows.
        try:
            for code, info in parse_camt053_balances(xml_data):
                captured.append(
                    {
                        "type": _CAMT053_TYPE_MAP.get(code, code),
                        "date": fmt_date(info["date"]),
                        "value": encode_value(info["value"], info["currency"]),
                    }
                )
        except Exception:  # noqa: BLE001 - capture must never break txns
            pass
        return original_camt053(xml_data, translate=translate)

    fints.client.mt940_to_array = _patched_mt940_to_array
    fints.client.camt053_to_dict = _patched_camt053_to_dict
    try:
        yield captured
    finally:
        fints.client.mt940_to_array = original_mt940
        fints.client.camt053_to_dict = original_camt053
