"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `from gls_fints import ...` without an editable install (handy when
# running tests against a fresh checkout). pyproject.toml's
# [tool.setuptools.packages.find] also picks this up for real installs.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def sample_mt940() -> str:
    """A minimal two-day MT940 statement with intermediate balances.

    Day 1: 1000.00 EUR opening, one 150.00 debit, 850.00 closing.
    Day 2: 850.00 EUR opening, one 200.00 credit, 1050.00 closing,
           plus an :64: available balance.
    """
    return (
        ":20:STARTUMSE\r\n"
        ":25:43060967/1310166000\r\n"
        ":28C:0/1\r\n"
        ":60F:C251025EUR1000,00\r\n"
        ":61:2510251025DR150,00NDDTNONREF\r\n"
        ":86:166?00BASISLASTSCHRIFT?20Payment 1?32ACME GmbH"
        "?31DE12345678901234567890\r\n"
        ":62M:C251025EUR850,00\r\n"
        ":60M:C251026EUR850,00\r\n"
        ":61:2510261026CR200,00NTRFNONREF\r\n"
        ":86:051?00UEBERWEISUNG?20Salary?32Employer GmbH\r\n"
        ":62F:C251026EUR1050,00\r\n"
        ":64:C251026EUR1050,00\r\n"
    )


@pytest.fixture
def sample_camt053() -> bytes:
    """A minimal camt053 payload with all balance types we map."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Acct><Id><IBAN>DE24430609671310166000</IBAN></Id><Ccy>EUR</Ccy></Acct>
      <Bal>
        <Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">1000.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Dt><Dt>2025-10-25</Dt></Dt>
      </Bal>
      <Bal>
        <Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">1050.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Dt><Dt>2025-10-26</Dt></Dt>
      </Bal>
      <Bal>
        <Tp><CdOrPrtry><Cd>CLAV</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">1050.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Dt><Dt>2025-10-26</Dt></Dt>
      </Bal>
      <Bal>
        <Tp><CdOrPrtry><Cd>FWAV</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">100.50</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Dt><Dt>2025-10-27</Dt></Dt>
      </Bal>
      <Bal>
        <Tp><CdOrPrtry><Cd>ITBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">900.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Dt><DtTm>2025-10-25T23:59:59</DtTm></Dt>
      </Bal>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""
