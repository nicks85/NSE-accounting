# Kosh — Offline Indian Share-Market Tax Calculator

> Free, open-source, fully offline desktop app that turns your broker statements into
> filing-ready capital gains and trading income figures for Indian Income Tax.
> Your data never leaves your computer.

**Status:** pre-alpha · **Licence:** AGPL-3.0 · **Not tax advice** — verify with a Chartered Accountant before filing.

---

## Why

Most Indian investors either pay a cloud service or upload their tradebook to a website they
can't verify. Kosh runs entirely on your machine, makes zero network calls, and shows the legal
section behind every number it computes.

## Features (MVP)

| Area | Scope |
|---|---|
| Import | Zerodha, Groww, Upstox, Angel One tradebooks/Tax P&L (CSV/XLSX); CAMS/KFintech CAS (PDF) for mutual funds |
| Matching | FIFO lot matching per ISIN, corporate actions (splits, bonus) |
| Equity capital gains | STCG / LTCG, ₹1.25 lakh LTCG exemption, 31-Jan-2018 grandfathering, 23-Jul-2024 rate cutover |
| Mutual funds | Equity vs specified (debt) funds, deemed short-term rules |
| Trading income | Intraday = speculative business income; F&O = non-speculative business income; turnover calc |
| Losses | Set-off rules and 8-year carry-forward ledger across tax years |
| Dual law support | Income-tax Act 1961 (up to FY 2025-26) and Income-tax Act 2025 (Tax Year 2026-27 onward) |
| Output | Schedule CG / 112A breakdown, ITR-2 / ITR-3 JSON export, PDF summary, tax-loss harvesting suggestions |

## Privacy guarantees

- No telemetry, no update checks, no network permission in the app manifest.
- All data stored in a local SQLite file you control.
- Reproducible, signed builds so anyone can verify the binary matches the source.

## Architecture

```
kosh/
├── engine/            # Python rules engine (pure, deterministic, Decimal-only)
│   ├── rules/         # Versioned rule packs: fy2024_25.py, fy2025_26.py, ty2026_27.py
│   ├── matching/      # FIFO lot matcher, corporate actions
│   ├── classify/      # Capital gains vs speculative vs non-speculative
│   └── export/        # ITR JSON (against official CBDT schemas), PDF
├── importers/         # One parser per broker / statement type
├── app/               # Tauri desktop shell + web UI (React)
├── tests/
│   ├── fixtures/      # SYNTHETIC broker files only — never real data
│   └── golden/        # Worked examples with expected tax, CA-reviewed
└── docs/rules/        # Plain-English explanation of each rule with section citations
```

## Quick start (developers)

```bash
git clone https://github.com/nicks85/NSE-accounting.git && cd NSE-accounting
uv sync                      # Python engine deps
pnpm install --dir app       # UI deps
uv run pytest                # engine tests
pnpm --dir app dev           # UI in the browser, using the local engine
KOSH_ENGINE_PYTHON="$(uv run which python)" pnpm --dir app tauri dev   # desktop app (needs Rust)
```

See `CONTRIBUTING.md` for the full workflow and `docs/RELEASING.md` for releases and how to
verify a download.

## Contributing

Tax rules change every Budget. The most valuable contributions are:
1. New or corrected rule packs with section citations.
2. Golden test cases (synthetic data + expected output).
3. Importers for more brokers.

Every rule change must include a citation and a test. See `CONTRIBUTING.md`.

## Disclaimer

Kosh is a calculation aid, not tax, legal or financial advice. The authors accept no liability
for filings made using it. Always verify figures against your AIS/TIS and with a qualified CA.
