# 0002 — Import broker files that have no ISIN without asking the user

- **Status:** approved 2026-10-07 (option 2 of the choices given in chat)
- **Scope:** `importers/angel_one.py`, `engine/rpc.py`, `engine/export/itr.py`, Import screen
- **Related:** brief 0001 (D7, task 11), Q-030, Q-032

## Problem

Angel One's trade history names each company by a truncated name ("EXAMPLE DEPO SER (I)") and
has no ISIN. The first version of the importer refused to import until the user typed an ISIN
for every name: 14 boxes for one year's file. The user expects upload-and-go.

## Options

| Option | For | Against |
|---|---|---|
| 1. Bundled company list fills ISINs automatically and only unmatched names are listed | Best end state | Needs the NSE/BSE terms checked and a matcher for truncated names: more work before the user can import anything |
| **2. Import with the company name as the key; never ask** | Immediate. FIFO and gains don't need the ISIN. | Shares from another broker or the CAS (keyed by ISIN) aren't recognised as the same share. Grandfathered (pre-2018) Schedule 112A rows can't be exported. ETFs can't be recognised from their name. |
| 3. Keep asking | Exact | An extra step the user rejected |

## Decision: option 2

- **Instrument key:** a scrip without an ISIN is keyed `NAME:<NAME IN CAPITALS, SINGLE SPACES>`.
  The engine still accepts an optional name → ISIN map, so option 1 can fill ISINs later
  without changing the importer.
- **ETF warning:** names that look like an ETF or fund (ETF, BEES, FUND, GOLD, SILVER, LIQUID)
  get a warning. Without an ISIN Kosh treats them as listed shares, which is wrong for gold,
  debt and international ETFs (Q-024).
- **Schedule 112A:** the export requires an ISIN only for shares acquired on or before
  31-Jan-2018. That is the official schema's rule: later acquisitions use the consolidated
  `INNOTREQUIRD` row. A pre-2018 share imported by name stops the export with a message naming
  it.
- **Known limitation:** the same company imported from Zerodha (by ISIN) and Angel One (by
  name) is held as two instruments. This is harmless while FIFO runs per demat account (brief
  0001 D4) but matters for a transfer between accounts. Option 1 removes it.
- **Follow-up:** brief 0001 task 11, the bundled company list, upgrades `NAME:` keys to ISINs
  automatically.
