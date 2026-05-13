"""Tests for fints_fetch.cli and fints_fetch.bank."""

from __future__ import annotations

from datetime import date

import pytest

from fints_fetch.bank import find_by_blz, find_by_name, resolve_bank
from fints_fetch.cli import (
    _resolve_enddate,
    build_parser,
    normalize_iban,
    parse_iban_filter,
)


class TestNormalizeIban:
    def test_lowercase_uppercased(self):
        assert normalize_iban("de24430609671310166000") == "DE24430609671310166000"

    def test_internal_spaces_removed(self):
        assert normalize_iban("DE24 4306 0967 1310 1660 00") == "DE24430609671310166000"

    def test_leading_trailing_whitespace_trimmed(self):
        assert normalize_iban("  DE24430609671310166000  ") == "DE24430609671310166000"

    def test_tabs_and_newlines(self):
        assert normalize_iban("DE24\t4306\n0967") == "DE2443060967"


class TestParseIbanFilter:
    def test_empty_input(self):
        assert parse_iban_filter([]) == set()

    def test_single_iban(self):
        assert parse_iban_filter(["DE12"]) == {"DE12"}

    def test_comma_separated(self):
        assert parse_iban_filter(["DE12,DE34"]) == {"DE12", "DE34"}

    def test_multiple_args(self):
        assert parse_iban_filter(["DE12", "DE34"]) == {"DE12", "DE34"}

    def test_mix_of_separators(self):
        assert parse_iban_filter(["de12,DE34", " de56 "]) == {
            "DE12", "DE34", "DE56"
        }

    def test_empty_entries_are_dropped(self):
        assert parse_iban_filter(["DE12,,", ",DE34"]) == {"DE12", "DE34"}

    def test_duplicates_collapsed(self):
        assert parse_iban_filter(["DE12", "de12", "  DE12  "]) == {"DE12"}


class TestBuildParser:
    def test_defaults(self):
        ns = build_parser().parse_args([])
        assert ns.iban == []
        assert ns.days == 30
        assert ns.enddate is None
        assert ns.persist_state is False
        assert ns.verbose is False
        assert ns.bank is None
        assert ns.blz is None

    def test_bank_flag(self):
        ns = build_parser().parse_args(["--bank", "gls"])
        assert ns.bank == "gls"

    def test_blz_flag(self):
        ns = build_parser().parse_args(["--blz", "43060967"])
        assert ns.blz == "43060967"

    def test_iban_repeats_into_list(self):
        ns = build_parser().parse_args(["--iban", "DE1", "--iban", "DE2"])
        assert ns.iban == ["DE1", "DE2"]

    def test_iban_accepts_comma_separated_value(self):
        ns = build_parser().parse_args(["--iban", "DE1,DE2"])
        assert ns.iban == ["DE1,DE2"]
        assert parse_iban_filter(ns.iban) == {"DE1", "DE2"}

    def test_days_is_int(self):
        ns = build_parser().parse_args(["--days", "90"])
        assert ns.days == 90 and isinstance(ns.days, int)

    def test_persist_state_is_a_flag(self):
        ns = build_parser().parse_args(["--persist-state"])
        assert ns.persist_state is True

    def test_invalid_days_rejected(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--days", "not-a-number"])


class TestResolveEnddate:
    def test_none_returns_today(self):
        assert _resolve_enddate(None) == date.today()

    def test_yyyymmdd_string(self):
        assert _resolve_enddate("20251101") == date(2025, 11, 1)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _resolve_enddate("2025-11-01")


class TestFindByBlz:
    def test_gls_known_blz(self):
        blz, url = find_by_blz("43060967")
        assert blz == "43060967"
        assert url.startswith("https://")

    def test_unknown_blz_raises(self):
        with pytest.raises(ValueError, match="No bank found for BLZ"):
            find_by_blz("00000000")

    def test_strips_whitespace(self):
        blz, _ = find_by_blz("  43060967  ")
        assert blz == "43060967"


class TestFindByName:
    def test_case_insensitive(self):
        blz, url = find_by_name("GLS Gemeinschaftsbank")
        assert blz == "43060967"

    def test_lowercase_substring(self):
        blz, _ = find_by_name("gls gemeinschaftsbank")
        assert blz == "43060967"

    def test_short_unambiguous_substring(self):
        # 'gls gemeinschaft' is a unique substring of one bank name
        blz, _ = find_by_name("gls gemeinschaft")
        assert blz == "43060967"

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="No bank found matching"):
            find_by_name("zzz nonexistent bank xyz")

    def test_ambiguous_name_raises(self):
        # 'sparkasse' matches many banks
        with pytest.raises(ValueError, match="banks match"):
            find_by_name("sparkasse")


class TestResolveBank:
    def test_blz_takes_precedence_over_bank(self):
        blz, _ = resolve_bank(blz="43060967", bank="irrelevant name")
        assert blz == "43060967"

    def test_blz_only(self):
        blz, url = resolve_bank(blz="43060967")
        assert blz == "43060967"
        assert url.startswith("https://")

    def test_bank_only(self):
        blz, url = resolve_bank(bank="gls gemeinschaftsbank")
        assert blz == "43060967"
        assert url.startswith("https://")

    def test_url_override(self):
        _, url = resolve_bank(blz="43060967", url="https://custom.example.com/fints")
        assert url == "https://custom.example.com/fints"

    def test_neither_raises(self):
        with pytest.raises(ValueError, match="Specify a bank"):
            resolve_bank()
