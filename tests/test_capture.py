"""Tests for fints_fetch.capture.

The bulk of the historical bugs in this codebase have been in here: the
empty-Balance2 case, the camt053-vs-MT940 detection, and the
shallow-copy leak in mt940. Every one of those gets a regression test.
"""

from __future__ import annotations

import pytest

import fints.camt_parser
import fints.client
import fints.utils
import mt940.models

from fints_fetch.capture import capture_balances, parse_camt053_balances


# --- MT940 path ------------------------------------------------------------


class TestMT940Capture:
    def test_captures_all_six_balance_kinds(self, sample_mt940):
        with capture_balances() as captured:
            txs = fints.client.mt940_to_array(sample_mt940)

        # Both transactions parsed normally
        assert len(txs) == 2

        # Five distinct balance entries in document order:
        # :60F: -> finalOpening
        # :62M: -> intermediateClosing
        # :60M: -> intermediateOpening
        # :62F: -> finalClosing
        # :64:  -> available
        types = [e["type"] for e in captured]
        assert types == [
            "finalOpening",
            "intermediateClosing",
            "intermediateOpening",
            "finalClosing",
            "available",
        ]

    def test_balance_dates_match_statement(self, sample_mt940):
        with capture_balances() as captured:
            fints.client.mt940_to_array(sample_mt940)
        # Day 1 closes 20251025; day 2 opens 20251026.
        by_type = {e["type"]: e for e in captured}
        assert by_type["finalOpening"]["date"] == "20251025"
        assert by_type["intermediateClosing"]["date"] == "20251025"
        assert by_type["intermediateOpening"]["date"] == "20251026"
        assert by_type["finalClosing"]["date"] == "20251026"

    def test_balance_values_are_url_encoded_fractions(self, sample_mt940):
        with capture_balances() as captured:
            fints.client.mt940_to_array(sample_mt940)
        values = {e["type"]: e["value"] for e in captured}
        assert values["finalOpening"] == "100000%2F100%3AEUR"
        assert values["finalClosing"] == "105000%2F100%3AEUR"


# --- camt053 path ----------------------------------------------------------


class TestCamt053Capture:
    def test_captures_all_balance_types(self, sample_camt053):
        # The original camt053_to_dict expects <Rpt>/<Acct>/<Ccy> in a
        # particular nesting that our minimal sample doesn't include, so
        # it will raise from inside the patched call. The patched
        # capture runs FIRST, so balances are still captured before the
        # original blows up. We swallow that for this test.
        with capture_balances() as captured:
            with pytest.raises(IndexError):
                fints.client.camt053_to_dict(sample_camt053)

        types = [e["type"] for e in captured]
        assert types == [
            "opening",          # OPBD
            "closing",          # CLBD
            "closingAvailable", # CLAV
            "forwardAvailable", # FWAV
            "interim",          # ITBD (with <DtTm> instead of <Dt>)
        ]

    def test_credit_debit_sign(self, sample_camt053):
        # FWAV in the fixture is DBIT 100.50; should come through negative.
        with capture_balances() as captured:
            with pytest.raises(IndexError):
                fints.client.camt053_to_dict(sample_camt053)
        fwav = next(e for e in captured if e["type"] == "forwardAvailable")
        assert fwav["value"] == "-10050%2F100%3AEUR"

    def test_dttm_variant_parses_just_the_date(self, sample_camt053):
        with capture_balances() as captured:
            with pytest.raises(IndexError):
                fints.client.camt053_to_dict(sample_camt053)
        interim = next(e for e in captured if e["type"] == "interim")
        assert interim["date"] == "20251025"

    def test_unknown_balance_type_passes_through_code(self):
        # A non-ISO type code should appear verbatim rather than be dropped.
        xml = b"""<?xml version="1.0"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt>
    <Bal>
      <Tp><CdOrPrtry><Cd>XYZQ</Cd></CdOrPrtry></Tp>
      <Amt Ccy="EUR">42.00</Amt>
      <CdtDbtInd>CRDT</CdtDbtInd>
      <Dt><Dt>2025-10-25</Dt></Dt>
    </Bal>
  </Stmt></BkToCstmrStmt>
</Document>
"""
        with capture_balances() as captured:
            with pytest.raises(Exception):
                fints.client.camt053_to_dict(xml)
        assert [e["type"] for e in captured] == ["XYZQ"]

    def test_proprietary_type_via_Prtry_element(self):
        # The Tp may use <Prtry> instead of <Cd> for non-standard codes.
        xml = b"""<?xml version="1.0"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt>
    <Bal>
      <Tp><CdOrPrtry><Prtry>BANK_PRTRY</Prtry></CdOrPrtry></Tp>
      <Amt Ccy="EUR">10.00</Amt>
      <CdtDbtInd>CRDT</CdtDbtInd>
      <Dt><Dt>2025-10-25</Dt></Dt>
    </Bal>
  </Stmt></BkToCstmrStmt>
</Document>
"""
        with capture_balances() as captured:
            with pytest.raises(Exception):
                fints.client.camt053_to_dict(xml)
        assert [e["type"] for e in captured] == ["BANK_PRTRY"]


# --- parse_camt053_balances directly (no fints involvement) ---------------


class TestParseCamt053Balances:
    def test_skips_malformed_bal(self):
        # Missing CdtDbtInd should be silently skipped, not raised.
        xml = b"""<?xml version="1.0"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt>
    <Bal>
      <Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
      <Amt Ccy="EUR">100.00</Amt>
      <Dt><Dt>2025-10-25</Dt></Dt>
    </Bal>
  </Stmt></BkToCstmrStmt>
</Document>
"""
        # No CdtDbtInd, so the balance is rejected
        assert parse_camt053_balances(xml) == []

    def test_returns_empty_for_no_bal_elements(self):
        xml = b"""<?xml version="1.0"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt></Stmt></BkToCstmrStmt>
</Document>
"""
        assert parse_camt053_balances(xml) == []


# --- Cross-cutting invariants ---------------------------------------------


class TestCleanupAndIsolation:
    def test_patches_restored_after_exit(self, sample_mt940):
        # Capture the originals once at module load
        orig_mt940 = fints.utils.mt940_to_array
        orig_camt053 = fints.camt_parser.camt053_to_dict

        with capture_balances():
            # Inside the block the names in fints.client must NOT match
            # the originals.
            assert fints.client.mt940_to_array is not orig_mt940
            assert fints.client.camt053_to_dict is not orig_camt053

        # On exit, the names must point back at the originals exactly.
        assert fints.client.mt940_to_array is orig_mt940
        assert fints.client.camt053_to_dict is orig_camt053

    def test_patches_restored_after_exception(self):
        orig_mt940 = fints.utils.mt940_to_array
        orig_camt053 = fints.camt_parser.camt053_to_dict

        with pytest.raises(RuntimeError):
            with capture_balances():
                raise RuntimeError("boom")

        assert fints.client.mt940_to_array is orig_mt940
        assert fints.client.camt053_to_dict is orig_camt053

    def test_no_leak_across_runs(self, sample_mt940):
        # Regression: the previous implementation called
        # `setdefault(...).append(proc)` which mutated mt940's shared
        # DEFAULT_PROCESSORS lists, causing subsequent runs to see
        # processors from earlier runs.
        results = []
        for _ in range(3):
            with capture_balances() as captured:
                fints.client.mt940_to_array(sample_mt940)
            results.append(len(captured))

        assert results == [5, 5, 5], "captured lengths leaked between runs"

    def test_does_not_mutate_mt940_default_processors(self, sample_mt940):
        # Take a snapshot of the relevant default lists before and after.
        keys = (
            "post_final_opening_balance",
            "post_final_closing_balance",
            "post_intermediate_opening_balance",
            "post_intermediate_closing_balance",
            "post_available_balance",
            "post_forward_available_balance",
        )
        before = {
            k: list(mt940.models.Transactions.DEFAULT_PROCESSORS[k]) for k in keys
        }

        for _ in range(3):
            with capture_balances():
                fints.client.mt940_to_array(sample_mt940)

        after = {
            k: list(mt940.models.Transactions.DEFAULT_PROCESSORS[k]) for k in keys
        }
        assert before == after, "DEFAULT_PROCESSORS was mutated"

    def test_outer_context_unaffected_by_inner(self, sample_mt940):
        # Nesting should not cause the outer captured list to lose its
        # contents when the inner context exits.
        with capture_balances() as outer:
            fints.client.mt940_to_array(sample_mt940)
            outer_after_first = list(outer)
            with capture_balances() as inner:
                fints.client.mt940_to_array(sample_mt940)
            assert len(inner) == 5

        # The inner block also captured into the outer list, since both
        # patches are active concurrently — that's acceptable; the
        # important property is that the outer list still HOLDS what we
        # already captured. (If we ever change this, the test changes.)
        assert outer[:5] == outer_after_first


# --- Transactions parse correctness through patched fns -------------------


class TestTransactionsStillParseCorrectly:
    def test_mt940_transactions_returned_unchanged(self, sample_mt940):
        # Patched parsing must not affect the transactions list — only
        # add the side-channel balance capture.
        with capture_balances():
            patched = fints.client.mt940_to_array(sample_mt940)
        original = fints.utils.mt940_to_array(sample_mt940)
        assert len(patched) == len(original) == 2
        for p, o in zip(patched, original):
            assert p.data["amount"].amount == o.data["amount"].amount
            assert p.data["amount"].currency == o.data["amount"].currency
