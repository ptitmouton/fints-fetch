"""FinTS client orchestration: TAN handling, state persistence, fetching."""

from __future__ import annotations

import os
import signal
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fints.client import FinTS3PinTanClient, NeedTANResponse
from fints.segments.saldo import HKSAL5, HKSAL6, HKSAL7

from .capture import capture_balances
from .output import (
    amount_entry,
    hbci_balance_entry,
    mt940_balance_entry,
    transaction_entry,
)


# --- Configuration -----------------------------------------------------------

# Placeholder product ID. For anything beyond casual personal use, register
# your own at https://www.hbci-zka.de/register/prod_register.htm and set
# FINTS_PRODUCT_ID. Some banks reject calls without a registered ID.
DEFAULT_PRODUCT_ID = os.environ.get(
    "FINTS_PRODUCT_ID", "9FA6681DEC0CF3046BFC2F8A6"
)

# Where to keep the persisted dialog state (system ID, TAN mechanism,
# TAN medium).
DEFAULT_STATE_FILE = Path(
    os.environ.get("FINTS_STATE_FILE", str(Path.home() / ".fints_state"))
)


# --- TAN handling ------------------------------------------------------------

_DECOUPLED_TIMEOUT = 300  # seconds


def _wait_for_decoupled_confirmation() -> None:
    """Block until Enter is pressed, or raise TimeoutError after 5 minutes.

    Uses SIGALRM on Unix. On platforms without it (Windows) the timeout is
    skipped and the prompt blocks indefinitely, preserving existing behaviour.
    """
    if not hasattr(signal, "SIGALRM"):
        input("After approving the request in your banking app, press Enter... ")
        return

    def _handle_alarm(signum, frame):  # noqa: ARG001
        raise TimeoutError(
            f"No confirmation received after {_DECOUPLED_TIMEOUT // 60} minutes."
        )

    old_handler = signal.signal(signal.SIGALRM, _handle_alarm)
    signal.alarm(_DECOUPLED_TIMEOUT)
    try:
        input("After approving the request in your banking app, press Enter... ")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def ask_for_tan(client: FinTS3PinTanClient, response: NeedTANResponse) -> Any:
    """Resolve a single NeedTANResponse via stdin.

    For decoupled TAN methods (e.g. SecureGo plus) the bank has already
    pushed the request to the user's phone; we just wait for approval.
    For chip-card / SMS TANs we prompt on stdin.
    """
    print("\n--- TAN required ---")
    print(response.challenge)

    if response.decoupled:
        _wait_for_decoupled_confirmation()
        return client.send_tan(response, "")
    tan = input("Enter TAN: ").strip()
    return client.send_tan(response, tan)


def resolve_tan(client: FinTS3PinTanClient, response: Any) -> Any:
    """Loop ask_for_tan until the operation has produced its real result."""
    while isinstance(response, NeedTANResponse):
        response = ask_for_tan(client, response)
    return response


# --- State persistence -------------------------------------------------------


def load_state(path: Path = DEFAULT_STATE_FILE) -> bytes | None:
    """Read the persisted dialog state, or None if it doesn't exist."""
    if path.exists():
        return path.read_bytes()
    return None


def save_state(data: bytes, path: Path = DEFAULT_STATE_FILE) -> None:
    """Write the dialog state with 0600 perms; ignore chmod errors (e.g. on FAT)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# --- Per-account fetching ----------------------------------------------------


def fetch_hisal(client: FinTS3PinTanClient, account: Any) -> Any:
    """Issue HKSAL and return the raw HISAL response segment.

    The public ``client.get_balance()`` only exposes the booked balance,
    so we drop one level and parse the response ourselves to also pick
    up ``balance_pending`` (noted balance), line_of_credit, etc. Uses
    the highest HKSAL version the bank advertises.
    """
    with client._get_dialog() as dialog:
        hksal = client._find_highest_supported_command(HKSAL5, HKSAL6, HKSAL7)
        seg = hksal(
            account=hksal._fields["account"].type.from_sepa_account(account),
            all_accounts=False,
        )

        def _parse(command_seg, response):  # noqa: ARG001
            for resp in response.response_segments(command_seg, "HISAL"):
                return resp
            return None

        return client._send_with_possible_retry(dialog, seg, _parse)


def fetch_account_info(
    client: FinTS3PinTanClient,
    account: Any,
    days: int,
    enddate: date,
) -> dict[str, Any]:
    """Fetch balances + transactions for ``account`` and shape them as JSON.

    Returns a dict matching the ``account_info`` shape documented in the
    README. Errors on any individual sub-fetch are reported under
    ``currentBalanceError`` / ``transactionError`` and do not abort the
    other sub-fetches.
    """
    info: dict[str, Any] = {
        "iban": account.iban,
        "currentBalance": [],
        "balance": [],
        "transaction": [],
    }

    try:
        booked = resolve_tan(client, client.get_balance(account))
        entry = mt940_balance_entry("booked", booked)
        if entry is not None:
            info["currentBalance"].append(entry)
    except Exception as exc:  # noqa: BLE001
        info["currentBalanceError"] = str(exc)

    try:
        hisal = fetch_hisal(client, account)
        if hisal is not None and not isinstance(hisal, NeedTANResponse):
            noted = hbci_balance_entry(
                "noted", getattr(hisal, "balance_pending", None)
            )
            if noted is not None:
                info["currentBalance"].append(noted)
            line_of_credit = amount_entry(getattr(hisal, "line_of_credit", None))
            if line_of_credit:
                info["lineOfCredit"] = line_of_credit
            available = amount_entry(getattr(hisal, "available_amount", None))
            if available:
                info["availableAmount"] = available
            used = amount_entry(getattr(hisal, "used_amount", None))
            if used:
                info["usedAmount"] = used
    except Exception:  # noqa: BLE001
        pass

    start = enddate - timedelta(days=days)
    try:
        with capture_balances() as captured:
            txs = resolve_tan(
                client,
                client.get_transactions(account, start_date=start, end_date=enddate),
            )
        info["transaction"] = [transaction_entry(tx, account) for tx in txs]
        info["balance"] = list(captured)
    except Exception as exc:  # noqa: BLE001
        info["transactionError"] = str(exc)

    return info
