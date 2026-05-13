"""Bank resolver: map bank name or BLZ to (blz, fints_url).

Uses the fints-url package which bundles the aqbanking bank database.
"""

from __future__ import annotations

import difflib

import fints_url as _fints_url_mod


def _banks() -> dict[str, dict]:
    return _fints_url_mod.__bank_info__


def find_by_blz(blz: str) -> tuple[str, str]:
    """Return (blz, fints_url) for an exact BLZ string."""
    info = _banks().get(blz.strip())
    if info is None:
        raise ValueError(f"No bank found for BLZ {blz!r}")
    return info["blz"], info["fints"]


def find_by_name(name: str) -> tuple[str, str]:
    """Case-insensitive substring search on bank name.

    Returns (blz, fints_url) for an unambiguous match.
    Raises ValueError with a helpful hint on 0 or multiple matches.
    """
    query = name.strip().lower()
    banks = _banks()
    matches = [info for info in banks.values() if query in info["name"].lower()]

    if len(matches) == 1:
        return matches[0]["blz"], matches[0]["fints"]

    if len(matches) > 1:
        # Prefer an exact full-name match
        exact = [m for m in matches if m["name"].lower() == query]
        if len(exact) == 1:
            return exact[0]["blz"], exact[0]["fints"]
        # Prefer a name that starts with the query
        starts = [m for m in matches if m["name"].lower().startswith(query)]
        if len(starts) == 1:
            return starts[0]["blz"], starts[0]["fints"]
        listed = ", ".join(f"{m['name']} ({m['blz']})" for m in matches[:5])
        suffix = f" and {len(matches) - 5} more" if len(matches) > 5 else ""
        raise ValueError(
            f"{len(matches)} banks match {name!r}: {listed}{suffix}. "
            "Use --blz to disambiguate."
        )

    # No substring match — suggest close names via fuzzy matching
    all_names = [info["name"] for info in banks.values()]
    close = difflib.get_close_matches(name, all_names, n=3, cutoff=0.5)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise ValueError(f"No bank found matching {name!r}.{hint}")


def resolve_bank(
    *,
    bank: str | None = None,
    blz: str | None = None,
    url: str | None = None,
) -> tuple[str, str]:
    """Return (blz, fints_url). Precedence: blz > bank.

    If *url* is provided it overrides the URL from the database lookup.
    Raises ValueError if neither blz nor bank is supplied.
    """
    if blz:
        resolved_blz, resolved_url = find_by_blz(blz)
    elif bank:
        resolved_blz, resolved_url = find_by_name(bank)
    else:
        raise ValueError(
            "Specify a bank via --bank NAME, --blz BLZ, "
            "or the FINTS_BANK / FINTS_BLZ environment variable."
        )
    return resolved_blz, url or resolved_url
