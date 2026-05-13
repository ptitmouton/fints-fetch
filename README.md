# gls-fints

A small command-line tool that talks to **GLS Bank** over the German
**FinTS / HBCI** protocol via [python-fints], fetches balances and
transactions for one or more accounts, and prints the result as JSON to
stdout. Built for the SecureGo plus *decoupled* TAN flow ("Direktfreigabe")
that GLS made mandatory on 2025-08-01.

This is a developer-oriented proof of concept. It is not a banking app
and it is not for production payments — read [§ Limitations](#limitations)
before relying on it.

---

## Quick start

```bash
pip install -e .

export GLS_USER='YourVRNetKey'           # or your alias
export GLS_PIN='YourOnlineBankingPIN'    # optional; otherwise prompted
gls-fints --days 30
```

The first time you connect, python-fints needs to pick a TAN mechanism
(choose **SecureGo plus**) and possibly a TAN medium. Pass
`--persist-state` to remember the choice for next time. After the very
first run and again roughly every 90 days, the bank will require a TAN on
login per PSD2.

Pipe the JSON straight into something useful:

```bash
gls-fints --days 90 \
  | jq '.[] | .account_info | {iban, txns: (.transaction | length)}'
```

---

## CLI

```text
gls-fints [-h] [--iban IBAN] [--days DAYS] [--enddate ENDDATE]
          [--persist-state] [-v] [--version]
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--iban` | all | Repeat the flag or pass `DE12...,DE34...`. Whitespace and case are normalised, so pasting from a statement is fine. |
| `--days` | `30` | Number of days of transactions to fetch, ending at `--enddate`. |
| `--enddate` | today | `YYYYMMDD`. Transactions are fetched for `[enddate-days, enddate]`. |
| `--persist-state` | off | Saves the chosen TAN mechanism / medium and the dialog system ID to `$FINTS_STATE_FILE` so later runs don't re-prompt. The PIN is **never** persisted. |
| `-v` / `--verbose` | off | Forward python-fints INFO logs to stderr. |

Status messages go to **stderr**, JSON goes to **stdout**, so piping is safe.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GLS_USER` | _prompt_ | VR-NetKey or alias |
| `GLS_PIN` | _prompt_ | Online banking PIN |
| `FINTS_PRODUCT_ID` | placeholder | **Strongly recommended.** Register one at <https://www.hbci-zka.de/register/prod_register.htm>; some banks reject calls without a registered ID. |
| `FINTS_BLZ` | `43060967` | GLS Bank. Other Atruvia banks should also work. |
| `FINTS_URL` | `https://fints1.atruvia.de/cgi-bin/hbciservlet` | Atruvia endpoint. The old `hbci-pintan.gad.de` was retired in March 2024. |
| `FINTS_STATE_FILE` | `~/.fints_state` | Where `--persist-state` writes. |

---

## Output shape

A JSON array, one entry per account:

```json
[
  {
    "account_info": {
      "iban": "DE24430609671310166000",
      "currentBalance": [
        {"type": "booked", "date": "20251101", "value": "74670%2F100%3AEUR"},
        {"type": "noted",  "date": "20251101", "value": "74670%2F100%3AEUR"}
      ],
      "balance": [
        {"type": "finalOpening",        "date": "20251002", "value": "..."},
        {"type": "intermediateClosing", "date": "20251002", "value": "..."},
        {"type": "intermediateOpening", "date": "20251003", "value": "..."},
        {"type": "finalClosing",        "date": "20251101", "value": "..."},
        {"type": "available",           "date": "20251101", "value": "..."}
      ],
      "transaction": [
        {
          "date": "20251025",
          "valutaDate": "20251025",
          "value": "-15000%2F100%3AEUR",
          "localAccountNumber": "1310166000",
          "remoteIban": "DE12345678901234567890",
          "remoteName": "ACME GmbH",
          "purpose": "Payment 1",
          "endToEndReference": "E2E-123",
          "transactionCode": "116"
        }
      ]
    }
  }
]
```

### `currentBalance`

The HKSAL snapshot — at most two entries:

| `type`   | Source | Meaning |
| --- | --- | --- |
| `booked` | always | The current booked balance |
| `noted`  | optional | The noted (pending / vorgemerkt) balance, when the bank returns it |

### `balance`

Every per-statement balance the bank embedded in the transaction
response, in document order. The `type` codes differ slightly depending
on whether the bank serves MT940 (HKKAZ) or camt053 XML (HKCAZ):

| MT940 | camt053 | Meaning |
| --- | --- | --- |
| `finalOpening` (`:60F:`) | `opening` (OPBD) | Opening balance of the range |
| `intermediateOpening` (`:60M:`) | `interim` (ITBD) | Per-day / sub-statement opening |
| `finalClosing` (`:62F:`) | `closing` (CLBD) | Closing balance of the range |
| `intermediateClosing` (`:62M:`) | _(no separate code)_ | Per-day sub-statement closing |
| `available` (`:64:`) | `closingAvailable` (CLAV) | Currently available balance |
| `forwardAvailable` (`:65:`) | `forwardAvailable` (FWAV) | Future-dated available balance |
| — | `previouslyClosed` (PRCD) | Closing balance of the previous period |
| — | `interimAvailable` (ITAV) | Per-day available balance |
| — | `info` (INFO) | Informational |

Most modern Atruvia banks (GLS included) serve **camt053**. If your bank
splits the response per booking day, you'll see one opening + closing per
day; if it returns one statement covering the whole range, you'll only
see the range-wide variants. The `date` on each entry tells you which.

### Values

Values use HBCI4Java-style fractional notation, URL-encoded:

> `74670/100:EUR` → `74670%2F100%3AEUR` → 746.70 EUR

The denominator is always `100` (every currency we surface uses two
decimal places). Negative values are signed in the numerator.

### Dates

Plain `YYYYMMDD` strings.

### `transaction`

Each transaction includes every field python-fints / mt940 actually
populated, mapped to camelCase keys. Fields that the bank didn't fill in
are omitted rather than serialised as `null`. The full set of possible
keys: `date`, `valutaDate`, `value`, `localAccountNumber`, `remoteIban`,
`remoteBic`, `remoteName`, `recipientName`, `purpose`,
`additionalPurpose`, `postingText`, `endToEndReference`,
`customerReference`, `bankReference`, `mandateReference`, `creditorId`,
`purposeCode`, `transactionCode`, `primaNota`, `status`.

---

## Docker

```bash
docker build -t gls-fints .

docker run --rm -it \
  -e GLS_USER='YourVRNetKey' \
  -e GLS_PIN='YourOnlineBankingPIN' \
  -e FINTS_PRODUCT_ID='YourRegisteredProductID' \
  -v gls-fints-state:/state \
  gls-fints --days 30
```

The image is multi-stage, slim, and runs as a non-root user (UID 10001).
The `--persist-state` file goes to `/state` inside the container; mount
a named volume there if you want it to survive between runs. `-it` is
required because TAN prompts read from stdin.

---

## Development

```bash
pip install -e '.[dev]'
pytest -q                  # 67 tests, < 1 s
pytest --cov=gls_fints     # with branch coverage
```

The test suite focuses on the parts that have actually broken during
development: the per-statement balance capture (MT940 + camt053, no
leak across runs, cleanup on exception), the JSON shaping helpers
(empty-Balance2 edge cases, sign handling), and the CLI's IBAN
normalisation. It does **not** hit a real bank — that's left to manual
integration testing.

### Project layout

```
gls-fints/
├── pyproject.toml
├── Dockerfile
├── README.md
├── src/gls_fints/
│   ├── __init__.py
│   ├── __main__.py           # `python -m gls_fints`
│   ├── cli.py                # argparse + main()
│   ├── client.py             # TAN, state, fetch_account_info
│   ├── capture.py            # MT940 + camt053 balance capture
│   └── output.py             # encode_value, fmt_date, *_entry helpers
└── tests/
    ├── conftest.py           # fixtures (sample MT940 + camt053 payloads)
    ├── test_capture.py
    ├── test_cli.py
    └── test_output.py
```

---

## Limitations

- **Read-only.** The script only queries balances and transactions. No
  transfers, no standing orders, no SEPA payments. python-fints can do
  those, but this PoC explicitly doesn't.
- **GLS / Atruvia only.** Hard-coded URL and default BLZ. Other Atruvia
  banks may work by overriding `FINTS_BLZ` / `FINTS_URL`. Banks on a
  different processor (Finanz Informatik / Sparkassen, Fiducia, etc.)
  will need a different endpoint and may need other tweaks.
- **No product ID by default.** The placeholder might still work for
  light personal use but is not appropriate for shipped products. Register
  one and set `FINTS_PRODUCT_ID`.
- **Interactive TAN.** SecureGo plus decoupled approval still needs you
  to confirm on your phone. The non-decoupled fallback (e.g. Sm@rtTAN
  via chipcard reader) reads the TAN from stdin.
- **No retry / scheduling.** Designed to be run by hand or a wrapper
  (cron, systemd timer, CI). It doesn't handle rate limiting or partial
  failures beyond surfacing them in the JSON as `*Error` keys.

---

## License

MIT.

[python-fints]: https://github.com/raphaelm/python-fints
