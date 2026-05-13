"""Command-line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from collections.abc import Iterable
from datetime import date, datetime

from fints.client import FinTS3PinTanClient
from fints.utils import minimal_interactive_cli_bootstrap

from . import __version__
from .bank import resolve_bank
from .client import (
    DEFAULT_PRODUCT_ID,
    DEFAULT_STATE_FILE,
    fetch_account_info,
    load_state,
    resolve_tan,
    save_state,
)


# --- IBAN normalisation / filtering ------------------------------------------


def normalize_iban(value: str) -> str:
    """Strip whitespace, drop internal spaces, uppercase. Robust to pasted IBANs.

    >>> normalize_iban("  de12 5001 0517  ")
    'DE12500105170'
    """
    return "".join(value.split()).upper()


def parse_iban_filter(values: Iterable[str]) -> set[str]:
    """Turn argparse's list of --iban arguments into a set of normalised IBANs.

    Accepts both repeated ``--iban DE1 --iban DE2`` and a single
    ``--iban DE1,DE2``, plus any mix of the two.

    >>> sorted(parse_iban_filter(["DE12,DE34", " de56 "]))
    ['DE12', 'DE34', 'DE56']
    >>> parse_iban_filter([]) == set()
    True
    """
    ibans: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            normalised = normalize_iban(part)
            if normalised:
                ibans.add(normalised)
    return ibans


# --- argparse ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fints-fetch",
        description="Fetch bank balances and transactions via FinTS/HBCI.",
    )

    bank = p.add_argument_group(
        "bank selection",
        "Provide --bank or --blz (or set FINTS_BANK / FINTS_BLZ). "
        "--blz takes precedence.",
    )
    bank.add_argument(
        "--bank",
        metavar="NAME",
        default=None,
        help=(
            "Bank name, case-insensitive substring (e.g. 'gls', 'sparkasse berlin'). "
            "Env: FINTS_BANK."
        ),
    )
    bank.add_argument(
        "--blz",
        metavar="BLZ",
        default=None,
        help="Bank code (Bankleitzahl) for an exact lookup. Env: FINTS_BLZ.",
    )

    p.add_argument(
        "--iban",
        action="append",
        default=[],
        help=(
            "Optional IBAN to filter by. Pass multiple times or as a "
            "comma-separated list. If omitted, every account on the "
            "access is fetched."
        ),
    )
    p.add_argument(
        "--days",
        type=int,
        default=30,
        help="How many days of transactions to fetch (default: 30).",
    )
    p.add_argument(
        "--enddate",
        type=str,
        default=None,
        help=("Fetch transactions up to this end date (YYYYMMDD). Defaults to today."),
    )
    p.add_argument(
        "--persist-state",
        action="store_true",
        help=(
            "Persist non-private dialog state (system ID, TAN mechanism, "
            "TAN medium) to a file for the next run. Off by default."
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print INFO-level log messages to stderr.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _resolve_enddate(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y%m%d").date()


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    iban_filter = parse_iban_filter(args.iban)
    enddate = _resolve_enddate(args.enddate)

    # --- Resolve bank --------------------------------------------------------
    blz = args.blz or os.environ.get("FINTS_BLZ")
    bank = args.bank or os.environ.get("FINTS_BANK")
    url_override = os.environ.get("FINTS_URL")

    try:
        resolved_blz, fints_url = resolve_bank(bank=bank, blz=blz, url=url_override)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # --- Credentials ---------------------------------------------------------
    user = os.environ.get("FINTS_USER") or input("VR-NetKey / Alias: ").strip()
    pin = os.environ.get("FINTS_PIN") or getpass.getpass("Online-banking PIN: ")

    client = FinTS3PinTanClient(
        resolved_blz,
        user,
        pin,
        fints_url,
        product_id=DEFAULT_PRODUCT_ID,
        from_data=load_state(DEFAULT_STATE_FILE),
    )

    minimal_interactive_cli_bootstrap(client)

    with client:
        if client.init_tan_response:
            resolve_tan(client, client.init_tan_response)

        accounts = resolve_tan(client, client.get_sepa_accounts())

        if iban_filter:
            available = {normalize_iban(a.iban): a for a in accounts}
            missing = iban_filter - available.keys()
            if missing:
                print(
                    "Warning: requested IBAN(s) not found on this access: "
                    + ", ".join(sorted(missing)),
                    file=sys.stderr,
                )
            accounts = [available[i] for i in iban_filter if i in available]
            if not accounts:
                print("No matching accounts - nothing to do.", file=sys.stderr)
                return 1

        print(f"Fetching {len(accounts)} account(s)...", file=sys.stderr)

        output = [
            {
                "accountInfo": fetch_account_info(
                    client, account, days=args.days, enddate=enddate
                )
            }
            for account in accounts
        ]

        if args.persist_state:
            save_state(client.deconstruct(including_private=False), DEFAULT_STATE_FILE)

    json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
