# fints-fetch

A small command-line tool that talks to any German bank over the
**FinTS / HBCI** protocol via [python-fints], fetches balances and
transactions for one or more accounts, and prints the result as JSON to
stdout.

This is a developer-oriented proof of concept. It is not a banking app
and it is not for production payments — read [§ Limitations](#limitations)
before relying on it.

---

## Quick start

```bash
pip install -e .

export FINTS_USER='YourLoginAlias'
export FINTS_PIN='YourOnlineBankingPIN'
fints-fetch --bank 'gls' --days 30
```

`--bank` accepts a case-insensitive substring of the bank's name. Use
`--blz` for an exact Bankleitzahl lookup instead. The first time you
connect, python-fints needs to pick a TAN mechanism and possibly a TAN
medium; pass `--persist-state` to remember the choice for next time.
After the very first run and again roughly every 90 days, your bank will
require a TAN on login per PSD2.

Pipe the JSON straight into something useful:

```bash
fints-fetch --bank 'gls' --days 90 \
  | jq '.[] | .account_info | {iban, txns: (.transaction | length)}'
```

---

## CLI

```text
fints-fetch [-h] [--bank NAME] [--blz BLZ] [--iban IBAN] [--days DAYS]
            [--enddate ENDDATE] [--persist-state] [-v] [--version]
```

### Bank selection

Provide one of `--bank` or `--blz` (or the equivalent env vars). `--blz`
takes precedence.

| Flag          | Notes                                                                                                                                                                                                               |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--bank NAME` | Case-insensitive substring of the bank name, e.g. `gls`, `sparkasse berlin`. The lookup uses the [fints-url] database (~1 400 German banks). Raises an error if the name is ambiguous; use `--blz` to disambiguate. |
| `--blz BLZ`   | Exact eight-digit Bankleitzahl, e.g. `43060967`.                                                                                                                                                                    |

### Options

| Flag               | Default | Notes                                                                                                                                                  |
| ------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--iban`           | all     | Repeat the flag or pass `DE12...,DE34...`. Whitespace and case are normalised, so pasting from a statement is fine.                                    |
| `--days`           | `30`    | Number of days of transactions to fetch, ending at `--enddate`.                                                                                        |
| `--enddate`        | today   | `YYYYMMDD`. Transactions are fetched for `[enddate-days, enddate]`.                                                                                    |
| `--persist-state`  | off     | Saves the chosen TAN mechanism / medium and the dialog system ID to `$FINTS_STATE_FILE` so later runs don't re-prompt. The PIN is **never** persisted. |
| `-v` / `--verbose` | off     | Forward python-fints INFO logs to stderr.                                                                                                              |

Status messages go to **stderr**, JSON goes to **stdout**, so piping is safe.

### Environment variables

| Variable           | Default           | Purpose                                                                                                                                          |
| ------------------ | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `FINTS_BANK`       | —                 | Bank name (same substring matching as `--bank`)                                                                                                  |
| `FINTS_BLZ`        | —                 | Exact Bankleitzahl (overrides `FINTS_BANK`)                                                                                                      |
| `FINTS_URL`        | _(from database)_ | Override the FinTS endpoint URL directly                                                                                                         |
| `FINTS_USER`       | _prompt_          | Login alias / VR-NetKey                                                                                                                          |
| `FINTS_PIN`        | _prompt_          | Online banking PIN                                                                                                                               |
| `FINTS_PRODUCT_ID` | placeholder       | **Strongly recommended.** Register one at <https://www.hbci-zka.de/register/prod_register.htm>; some banks reject calls without a registered ID. |
| `FINTS_STATE_FILE` | `~/.fints_state`  | Where `--persist-state` writes.                                                                                                                  |

---

## Output shape

A JSON array, one entry per account:

```json
[
  {
    "accountInfo": {
      "iban": "DE24430609671310166000",
      "currentBalance": [
        { "type": "booked", "date": "20251101", "value": "74670%2F100%3AEUR" },
        { "type": "noted", "date": "20251101", "value": "74670%2F100%3AEUR" }
      ],
      "balance": [
        { "type": "finalOpening", "date": "20251002", "value": "..." },
        { "type": "intermediateClosing", "date": "20251002", "value": "..." },
        { "type": "intermediateOpening", "date": "20251003", "value": "..." },
        { "type": "finalClosing", "date": "20251101", "value": "..." },
        { "type": "available", "date": "20251101", "value": "..." }
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

| `type`   | Source   | Meaning                                                            |
| -------- | -------- | ------------------------------------------------------------------ |
| `booked` | always   | The current booked balance                                         |
| `noted`  | optional | The noted (pending / vorgemerkt) balance, when the bank returns it |

### `balance`

Every per-statement balance the bank embedded in the transaction
response, in document order. The `type` codes differ slightly depending
on whether the bank serves MT940 (HKKAZ) or camt053 XML (HKCAZ):

| MT940                           | camt053                   | Meaning                                |
| ------------------------------- | ------------------------- | -------------------------------------- |
| `finalOpening` (`:60F:`)        | `opening` (OPBD)          | Opening balance of the range           |
| `intermediateOpening` (`:60M:`) | `interim` (ITBD)          | Per-day / sub-statement opening        |
| `finalClosing` (`:62F:`)        | `closing` (CLBD)          | Closing balance of the range           |
| `intermediateClosing` (`:62M:`) | _(no separate code)_      | Per-day sub-statement closing          |
| `available` (`:64:`)            | `closingAvailable` (CLAV) | Currently available balance            |
| `forwardAvailable` (`:65:`)     | `forwardAvailable` (FWAV) | Future-dated available balance         |
| —                               | `previouslyClosed` (PRCD) | Closing balance of the previous period |
| —                               | `interimAvailable` (ITAV) | Per-day available balance              |
| —                               | `info` (INFO)             | Informational                          |

If your bank splits the response per booking day you'll see one opening +
closing per day; if it returns one statement covering the whole range
you'll only see the range-wide variants. The `date` on each entry tells
you which.

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
docker build -t fints-fetch .

docker run --rm -it \
  -e FINTS_USER='YourLoginAlias' \
  -e FINTS_PIN='YourOnlineBankingPIN' \
  -e FINTS_PRODUCT_ID='YourRegisteredProductID' \
  -v fints-fetch-state:/state \
  ghcr.io/ptitmouton/fints-fetch --bank gls --days 30
```

Pre-built multi-arch images (`linux/amd64`, `linux/arm64`) are published
to the GitHub Container Registry on every release:

```bash
docker pull ghcr.io/ptitmouton/fints-fetch
```

The image is multi-stage, slim, and runs as a non-root user (UID 10001).
The `--persist-state` file goes to `/state` inside the container; mount
a named volume there if you want it to survive between runs. `-it` is
required because TAN prompts read from stdin.

---

## Development

```bash
pip install -e '.[dev]'
pytest -q                     # 82 tests, < 1 s
pytest --cov=fints_fetch      # with branch coverage
```

The test suite focuses on the parts that have actually broken during
development: the per-statement balance capture (MT940 + camt053, no
leak across runs, cleanup on exception), the JSON shaping helpers
(empty-Balance2 edge cases, sign handling), the CLI's IBAN normalisation,
and the bank resolver (BLZ lookup, name substring matching, ambiguity
handling). It does **not** hit a real bank — that's left to manual
integration testing.

### Project layout

```
fints-fetch/
├── pyproject.toml
├── Dockerfile
├── README.md
├── src/fints_fetch/
│   ├── __init__.py
│   ├── __main__.py           # `python -m fints_fetch`
│   ├── bank.py               # BLZ / name → (blz, fints_url) resolver
│   ├── cli.py                # argparse + main()
│   ├── client.py             # TAN, state, fetch_account_info
│   ├── capture.py            # MT940 + camt053 balance capture
│   └── output.py             # encode_value, fmt_date, *_entry helpers
└── tests/
    ├── conftest.py           # fixtures (sample MT940 + camt053 payloads)
    ├── test_capture.py
    ├── test_cli.py           # includes bank resolver tests
    └── test_output.py
```

---

## Limitations

- **Read-only.** The tool only queries balances and transactions. No
  transfers, no standing orders, no SEPA payments. python-fints can do
  those, but this PoC explicitly doesn't.
- **German banks only.** FinTS / HBCI is a German standard. The bank
  database covers ~1 400 institutions, but banks on processors other than
  the ones in the database (or foreign banks) won't be found.
- **No product ID by default.** The placeholder might still work for
  light personal use but is not appropriate for shipped products. Register
  one and set `FINTS_PRODUCT_ID`.
- **Interactive TAN.** Decoupled TAN methods (e.g. SecureGo plus) still
  need you to confirm on your phone. Non-decoupled methods (e.g. Sm@rtTAN
  via chipcard reader) read the TAN from stdin.
- **No retry / scheduling.** Designed to be run by hand or a wrapper
  (cron, systemd timer, CI). It doesn't handle rate limiting or partial
  failures beyond surfacing them in the JSON as `*Error` keys.

---

## License

MIT.

[python-fints]: https://github.com/raphaelm/python-fints
[fints-url]: https://github.com/dr-duplo/python-fints-url
