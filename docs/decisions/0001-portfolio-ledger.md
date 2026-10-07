# 0001 — Persistent portfolio ledger, first-time setup and unified import

- **Status:** proposed, awaiting approval
- **Date:** 2026-10-06
- **Scope:** engine (new `engine/ledger/`), importers, RPC, UI import/holdings/settings
- **New open questions:** Q-026 to Q-032 in `docs/OPEN_QUESTIONS.md`

## Problem

Today Kosh keeps nothing between sessions. Trades, fund classes, 31-Jan-2018 prices and
brought-forward losses live in the page's memory and are lost when it closes. Each year the user
has to re-import their whole history. A sale whose purchase isn't in the imported files stops
the whole calculation with `InsufficientHoldingsError`.

The goal is that **the software calculates tax every year** from a permanent local record:

- The user imports only that year's broker file.
- Lots still open carry forward to the next year automatically.
- Losses carry forward without being retyped.
- A sale is never priced with a guessed cost.

## What already exists (reused, not rebuilt)

- **Charges and STT on every trade.** `Trade.charges` and `Trade.stt` are carried through
  matching. Charges are added to cost on buys and deducted as transfer expenses on sells, and
  STT is kept out (`COMPUTATION`: 1961 s.48, 2025 s.72(1), s.72(3)(b)). STT is deducted from
  business income (`STT_BUSINESS_DEDUCTION`: 1961 s.36(1)(xv), 2025 s.32(k)). Item 4 is mostly
  about keeping the breakdown per charge, not new tax logic.
- **Header-row search.** `importers/tabular._find_header` already scans the first rows for
  the header, which handles title and summary rows above the table.
- **Losses across years.** `compute_tax_years` already runs the set-off and carry-forward
  ledger over several years.
- **Column mapping.** The column-mapping importer stays as the fallback for unknown brokers.

## Decisions to make

### D1. Where the ledger lives and who writes it

| Option | For | Against |
|---|---|---|
| **A. Python engine owns the SQLite file (stdlib `sqlite3`)** | No new dependency or Tauri plugin. The plugin allow-list stays "dialog only". One code path for the browser and desktop builds. Testable with pytest. The engine is already one long-lived process. | Rust has to pass the data folder to the sidecar. |
| B. Rust `tauri-plugin-sql` | Native to Tauri | Adds a plugin and IPC surface. Logic is split across Rust, TypeScript and Python. The browser build would need a second store. |
| C. JSON file per tax year | Human-readable | No transactions, so undo and dedupe are fragile. Slow on large histories. |

**Recommendation: A.** The file is `kosh.sqlite` in the OS app-data folder. On macOS that's
`~/Library/Application Support/in.kosh.app/`; on Windows `%APPDATA%\in.kosh.app\`; on Linux
`~/.local/share/in.kosh.app/`.

- **Desktop:** Rust takes the path from `app_data_dir()` and passes it to the sidecar.
- **Browser build:** the engine works out the same path itself.

The file is never in the repo. `*.sqlite` will be added to `.gitignore` and to a test that
fails if one is committed.

### D2. Store trades or store lots?

| Option | For | Against |
|---|---|---|
| **A. Trades are the source of truth. Lots and gains are rebuilt by replaying FIFO, with a cached year-end snapshot.** | Undo of an import is just "delete that batch and replay". Importing an older file later corrects every year automatically. There is one matcher, the one already tested, so matching can't drift between years. | A replay costs time. Expected to be milliseconds for tens of thousands of trades (to be measured in task 1). |
| B. Mutable open-lots table carried forward each year | Close to how people think about it | Undo and late imports need complex rollback. Two sources of truth. |

**Recommendation: A.** "Open lots carry forward" becomes a view: the lots still open at 31
March of each year. A **year snapshot** stores each year's computed figures with a fingerprint
of the inputs.

When the user marks a year **filed**, its snapshot is frozen. If a later import or edit would
change a filed year's figures, Kosh shows the difference prominently. That can happen when an
older tradebook changes FIFO, and it may mean a revised return.

### D3. Duplicate detection

- **Key:** broker + exchange + segment + trade date + order ID + trade ID. Trade IDs are unique
  per exchange and day, not for all time, so trade ID alone, as today's import screen uses, can
  collide.
- **When a file has no IDs:** SHA-256 of date, time, instrument, side, quantity and price,
  plus how many times that combination has appeared so far in the file. Two genuine identical
  fills aren't merged, and re-importing the same file still matches row for row.
- **Same key, different details:** the import is refused with both rows shown, not overwritten.
- **Same file imported again:** the file's SHA-256 is stored per batch. A re-import is
  recognised before parsing and shown as "already imported on …".

### D4. Matching across brokers (needs your decision; see Q-026)

Today the matcher runs FIFO per instrument across all imported files.

The FIFO rule for demat holdings (1961 s.45(2A), 2025 s.67(7)(c)) is generally applied **per
demat account**. If you hold the same share at Zerodha and Groww, a sale at Groww should be
matched only against Groww purchases. Changing to per-account matching changes figures for
anyone with more than one demat account. Moving shares between your own accounts then needs a
"transfer" entry that keeps the original date and cost.

**Best guess to implement:** FIFO per demat account, marked UNVERIFIED (Q-026).

### D5. First-time setup: history before the first import

| Option | Gives cost? | Gives buy date? | Effort for the user | Risk |
|---|---|---|---|---|
| **a. Opening holdings** (form or CSV template: ISIN, quantity, buy date, buy price, charges; several rows per ISIN for several lots) | Yes | Yes | Medium | Typing errors. Mitigated by d) and by checking against the first sale. |
| b. Broker Tax P&L / capital-gains report | For sold lots only | Yes | Low | Broker's own matching and grandfathering; formats unknown (no samples). It doesn't cover shares still held. |
| **c. Older tradebooks, oldest first** | Yes | Yes | High (one file per year) | Best accuracy. Zerodha has data from 2017 onward only; older brokers may have closed. |
| d. Depository CAS (CDSL/NSDL) | No | No | Low | Confirms quantities only |

**Recommendation:**

- **c first, a for what c can't reach**, with d as the quantity cross-check.
- **b only as a source of opening lots for holdings already sold.** It isn't mixed with
  tradebook rows for the same period, because that would double-count.
- **Opening holdings are entered as trades.** Each one is stored as a buy with source
  `opening` and an "as of" date. It is then matched like any other buy, and the user can edit
  or undo it.

**Unmatched sales:** the matcher gets a non-raising mode. Any quantity sold without a matching
purchase becomes a **"missing purchase history"** item.

- **Form:** buy date, quantity, price and charges, plus "how acquired": bought, IPO, bonus,
  gift or inheritance, ESOP, transfer-in. Each answer becomes a `manual` trade linked to that
  sale.
- **The year's tax total is withheld** while any item is unresolved. The Summary shows
  "Incomplete: 2 sales missing purchase history" instead of a figure.
- **Export is disabled** for that year at the same time.
- **Excluding a sale:** the user may exclude it explicitly. The total then shows "excludes N
  sales (₹X sale value)" on screen, in the PDF and in the warnings (Q-031). Kosh never uses a
  zero cost.

### D6. Unified import screen

1. **Choose the broker** from a list: Zerodha, Upstox, Angel One, Groww, "Other (map
   columns)", or the CAS. The XLSX/CSV upload area is the same for every broker.
2. **Header detection:** the existing header search finds the table below any title or
   summary rows.
3. **Preview, nothing saved yet:**
   - trades found, of which new and already in the ledger
   - buys vs sells
   - date range
   - total charges by kind, compared with the file's own "Total Charges" summary (warning if
     they differ by more than ₹1)
   - scrips that still need an ISIN (D7)
   - rows skipped, with reasons
4. **Save to ledger** creates an import batch. If any scrip names are unconfirmed, it asks
   for them first.
5. **Import history:** file name, broker, date range, trades added, imported on, and **Undo**.

Under the hood the import RPC splits in two:

- `import.preview` parses and returns the preview plus a token.
- `import.commit` writes the batch in one transaction.

### D7. Scrip name to ISIN, offline

| Option | For | Against |
|---|---|---|
| **Bundled security master.** A dev-time script, like the CBDT schemas, builds `engine/data/securities.csv` (ISIN, symbol, company name, series, listing status) from the exchanges' public equity lists. It is refreshed each release. | Covers most current names. No runtime network. | Current names only: renamed, merged or delisted companies won't match. Redistribution terms need checking (Q-032 / question 5). Adds a few hundred kB. |
| **Fuzzy match** (stdlib `difflib` plus abbreviation expansion: DEPO → DEPOSITORY, SER → SERVICES, (I) → INDIA, LTD) | Handles truncated names like "EXAMPLE DEPO SER (I)" | Can be confidently wrong. |
| **User confirms each new name** and Kosh remembers it (`symbol_alias`) | Always correct once confirmed | One click per new scrip |

**Recommendation: all three together.** Kosh suggests up to 3 candidates from the master
(exact symbol, then fuzzy name). The user confirms or types an ISIN. The answer is remembered
per broker and raw name, so it is asked once ever.

- No mapping is used without confirmation, except exact ISIN or symbol matches.
- F&O contracts map to a contract symbol, not an ISIN, so they need no lookup.

### D8. Charges breakdown (item 4)

- **Storage:** each charge is kept per trade by kind: brokerage, GST, exchange turnover,
  SEBI, stamp duty, IPFT, other, STT.
- **Matching uses totals:** charges excluding STT, and STT, as today. The breakdown is shown
  in "Why?" and in the PDF.
- **Treatment, already implemented:**
  - Buys: charges are added to cost (cost of acquisition, 1961 s.48(ii), 2025 s.72(1)).
  - Sells: charges are transfer expenses (1961 s.48(i), 2025 s.72(1)).
  - STT is never deducted against capital gains (1961 s.48 proviso, 2025 s.72(3)(b)).
  - STT is deductible against intraday and F&O business income (1961 s.36(1)(xv), 2025
    s.32(k)).
- **Open points (Q-027):**
  - whether GST and stamp duty on a buy are part of cost;
  - how DP charges, which appear only in the ledger statement, are treated;
  - when charges become deductible business expenses for intraday and F&O.

### D9. Ledger statement upload (reconciliation only)

This is a separate section, never a source of prices. Debits and credits are matched to the
net value of each settlement day's imported trades. Three outputs:

- **Surfaced:** DP charges, interest on delayed payment or MTF, penalties, AMC, dividends,
  and payouts.
- **Flagged:** settlement days whose money movement doesn't match the trades. That usually
  means a missing tradebook or a missing segment.
- **Dividends:** shown only, as information. They are income from other sources, taxed at
  slab rates with TDS, outside Kosh's scope today.

Formats are unknown for every broker, so this waits for anonymised samples. Formats won't be
invented.

### D10. Residency per tax year (item 7)

This is an input per year: Resident, Resident but not ordinarily resident (RNOR), or
Non-resident. The default is Resident. Changes for a non-resident (all marked UNVERIFIED until
checked, Q-028):

- **No basic-exemption shortfall against special-rate gains.** A resident individual whose
  other income is below the basic exemption limit can reduce s.111A/s.112A/s.112 gains by the
  shortfall. A non-resident cannot.
  - Kosh doesn't know the user's other income, so this needs either an "other income" input
    or a warning only. The recommendation is the warning, plus an optional input later.
- **Rebate under s.87A is not available** to non-residents. Kosh doesn't compute it today
  (Q-012), so this is a warning only.
- **ITR form:** ITR-2 or ITR-3 as now. The residential-status field is in Part A, which Kosh
  doesn't fill (Q-025). Kosh shows the status to select.
- **TDS:** brokers deduct tax at source from a non-resident's capital gains. Kosh lists TDS
  from the ledger statement where present, so the user can match it with Form 26AS. The tax
  figure stays the gross liability.
- **Treaty (DTAA) relief:** not computed; shown as a warning.
- **The ₹1.25 lakh exemption and the 12.5% / 20% rates:** believed to apply equally to
  non-residents (to verify).

## Proposed database schema (SQLite, version 1)

Money and quantities are stored as **TEXT decimal strings** and read back into `Decimal`. They
are never stored as REAL (CLAUDE.md rule 2). Dates are ISO-8601 text. Every table that holds
the user's data is keyed by `profile_id`, so a household can keep separate people in one file
(question 1).

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, created_at, created_by_app_version

CREATE TABLE profile (
  id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE account (                 -- one demat/trading account (D4)
  id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profile(id),
  broker TEXT NOT NULL, label TEXT NOT NULL,
  UNIQUE (profile_id, broker, label)
);

CREATE TABLE instrument (
  id INTEGER PRIMARY KEY,
  isin TEXT UNIQUE,                    -- NULL for F&O contracts
  contract_symbol TEXT UNIQUE,         -- F&O only
  name TEXT, kind TEXT NOT NULL CHECK (kind IN ('EQUITY','ETF','MF','FNO')),
  CHECK ((isin IS NULL) <> (contract_symbol IS NULL))
);

CREATE TABLE symbol_alias (            -- remembered scrip-name → instrument (D7)
  broker TEXT NOT NULL, raw_name TEXT NOT NULL,
  instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  source TEXT NOT NULL CHECK (source IN ('exact','user')), confirmed_at TEXT NOT NULL,
  PRIMARY KEY (broker, raw_name)
);

CREATE TABLE import_batch (
  id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profile(id),
  account_id INTEGER REFERENCES account(id),
  kind TEXT NOT NULL CHECK (kind IN
       ('tradebook','cas','opening','manual','taxpnl','ledger_statement')),
  broker TEXT, file_name TEXT, file_sha256 TEXT,
  imported_at TEXT NOT NULL, date_from TEXT, date_to TEXT,
  rows_read INTEGER, trades_added INTEGER, duplicates_skipped INTEGER,
  charges_total TEXT, file_charges_total TEXT, warnings_json TEXT,
  undone_at TEXT,                      -- undo = set this and drop the batch's trades
  UNIQUE (profile_id, file_sha256)
);

CREATE TABLE trade (
  id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profile(id),
  batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  account_id INTEGER REFERENCES account(id),
  instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  raw_symbol TEXT,                     -- as written in the file
  exchange TEXT, segment TEXT NOT NULL CHECK (segment IN ('EQUITY','FNO','MF')),
  trade_date TEXT NOT NULL, executed_at TEXT,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity TEXT NOT NULL, price TEXT NOT NULL,
  order_id TEXT, trade_ref TEXT,       -- broker's trade ID
  how_acquired TEXT,                   -- opening/manual buys: BOUGHT, IPO, GIFT, ... (Q-029)
  resolves_trade_id INTEGER REFERENCES trade(id),   -- manual buy entered for a missing sale
  dedupe_key TEXT NOT NULL,            -- D3
  UNIQUE (profile_id, dedupe_key)
);

CREATE TABLE trade_charge (
  trade_id INTEGER NOT NULL REFERENCES trade(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN
       ('BROKERAGE','GST','EXCHANGE','SEBI','STAMP','IPFT','OTHER','STT')),
  amount TEXT NOT NULL,
  PRIMARY KEY (trade_id, kind)
);

CREATE TABLE corporate_action (
  id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  kind TEXT NOT NULL CHECK (kind IN ('SPLIT','BONUS')),
  effective_date TEXT NOT NULL, ratio_new TEXT NOT NULL, ratio_old TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('user','bundled'))
);

CREATE TABLE instrument_setting (      -- per person, not per year
  profile_id INTEGER NOT NULL, instrument_id INTEGER NOT NULL,
  fund_class TEXT, fmv_2018 TEXT, name_for_112a TEXT,
  PRIMARY KEY (profile_id, instrument_id)
);

CREATE TABLE excluded_sale (           -- D5: explicit, flagged exclusion
  trade_id INTEGER PRIMARY KEY REFERENCES trade(id) ON DELETE CASCADE,
  reason TEXT NOT NULL, excluded_at TEXT NOT NULL
);

CREATE TABLE brought_forward_loss (    -- losses from before the first ledger year
  id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL,
  kind TEXT NOT NULL, year_arose INTEGER NOT NULL, amount TEXT NOT NULL
);

CREATE TABLE year_setting (
  profile_id INTEGER NOT NULL, start_year INTEGER NOT NULL,
  residency TEXT NOT NULL DEFAULT 'RES' CHECK (residency IN ('RES','NOR','NRI')),
  return_filed_on_time INTEGER,        -- Q-011
  itr_form TEXT, filed_at TEXT,        -- filed_at set = year frozen (D2)
  PRIMARY KEY (profile_id, start_year)
);

CREATE TABLE year_snapshot (           -- cache + audit of each computed year (D2)
  profile_id INTEGER NOT NULL, start_year INTEGER NOT NULL,
  inputs_fingerprint TEXT NOT NULL, engine_version TEXT NOT NULL,
  computed_at TEXT NOT NULL, report_json TEXT NOT NULL, open_lots_json TEXT NOT NULL,
  PRIMARY KEY (profile_id, start_year)
);

CREATE TABLE statement_entry (         -- D9, reconciliation only
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  posted_on TEXT NOT NULL, description TEXT NOT NULL,
  debit TEXT, credit TEXT, category TEXT, matched_on TEXT
);
```

**Migrations:** numbered SQL files in `engine/ledger/migrations/`, applied in order inside a
transaction. A newer file opened by an older app is refused with "made by a newer version".

**Backup and restore:**

- **Backup:** `VACUUM INTO` writes a consistent single-file copy named
  `kosh-backup-YYYY-MM-DD.kosh`, saved via the native Save dialog.
- **Restore:** runs `PRAGMA integrity_check` and checks the schema version, keeps the current
  file as `kosh.sqlite.before-restore`, then replaces it.

## Build order (each is one branch and one small, reviewed change)

| # | Task | Depends on | Needs from you |
|---|---|---|---|
| 1 | `engine/ledger/` store: schema, migrations, decimal-text round trip, data-folder resolution, replay into `compute_tax_years`. Engine only; no UI change. | — | — |
| 2 | Import into the ledger: batches, dedupe (D3), import history, undo. RPC and Import-screen history table. | 1 | — |
| 3 | Persist settings: fund classes, FMV, 112A names, brought-forward losses, filed-on-time flag. The UI reads and writes the ledger instead of page memory. | 2 | — |
| 4 | Year snapshots and "filed" freeze, with a changed-after-filing warning | 3 | — |
| 5 | Backup and restore | 1 | Q4 (encryption) |
| 6 | Unmatched sales: non-raising matcher, "missing purchase history" form, total withheld, explicit exclusion | 2 | — |
| 7 | Opening holdings: form and CSV template | 6 | — |
| 8 | FIFO per demat account plus transfers between accounts (D4) | 2 | Q2 |
| 9 | Unified import screen with preview and charges check (D6) | 2 | — |
| 10 | Charges breakdown per trade (D8) | 2 | — |
| 11 | Security master: build script, bundled file, lookup, fuzzy suggestions, confirmation (D7) | 2 | Q5 |
| 12 | Angel One built-in layout and synthetic fixture | 9, 10, 11 | Q6 |
| 13 | Residency per year and its warnings (D10) | 3 | — |
| 14 | Broker Tax P&L import as opening lots | 7 | anonymised samples |
| 15 | Depository CAS (CDSL/NSDL) quantity check | 2 | anonymised sample |
| 16 | Ledger statement reconciliation (D9) | 2 | anonymised samples |

Tasks 1 to 7 deliver the core goal: import one year at a time, with nothing guessed. Tasks 8
to 13 can run in parallel after task 2. Tasks 14 to 16 are blocked on real file formats.

Every UI task is click-tested in the running app before it is committed. `HOWTOUSE.md` is
updated as each workflow changes.

## Questions for you

1. **More than one person per installation?** A family may want each person's portfolio kept
   apart. My recommendation: support profiles in the schema now (cheap) and show a profile
   switcher only if you want it.
2. **FIFO per demat account (D4, Q-026)?** Do I build per-account matching as the UNVERIFIED
   default, or keep matching across all accounts as today?
3. **How strict should missing purchase history be?** Withhold the year's total until it is
   resolved or explicitly excluded (my recommendation), or show a provisional total with a red
   warning?
4. **Encrypt the ledger and its backups with a passphrase?** This needs SQLCipher or similar,
   a new native dependency, which complicates reproducible builds. My recommendation: no for
   now, and rely on the operating system's disk encryption. Backups contain your full trading
   history.
5. **Security master source.** May I add a dev-time script that downloads the NSE and BSE
   public equity lists, as we did for the CBDT schemas, and bundle the result? Both
   exchanges' terms of use need reading for redistribution first. Alternatively, start with
   user confirmation only and no bundled master.
6. **Angel One details** I can't take from the header alone:
   - the date format in the `Date` column;
   - whether F&O rows put expiry, strike and option type into `Scrip/Contract`, and how;
   - where the "Total Charges" summary sits and what it is called;
   - whether one row is one trade or one order.

   Could you paste the summary rows and two data rows with numbers changed?
7. **Broker Tax P&L (option b):** which brokers' reports do you have? I need one anonymised
   sample of each before task 14.
8. **Residency:** a warning only (my recommendation), or also an "other income" input so Kosh
   can apply the basic-exemption shortfall for residents?
9. **Snapshots of filed years:** when a late import changes a year you've marked as filed,
   should Kosh only warn, or keep both the "as filed" and "revised" figures side by side?
