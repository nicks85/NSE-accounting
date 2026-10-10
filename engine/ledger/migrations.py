"""Ledger schema migrations (brief 0001, "Proposed database schema").

Each entry is (version, SQL), applied in order inside one transaction. Kept as Python strings
rather than ``.sql`` files so the bundled engine needs no extra data files. Never edit a
released migration: add a new one.

Money and quantities are TEXT decimal strings, never REAL (CLAUDE.md rule 2). Dates are
ISO-8601 text.

Differences from the brief's sketch, version 1:

- ``trade.source_id`` keeps the engine's trade id (e.g. ``ZERODHA:NSE:2025-06-02:123``) so a
  replayed ``Trade`` is identical to the imported one, and exclusions and hand-entered
  purchases can refer to it.
- ``instrument.isin`` also holds a ``NAME:`` placeholder for a share whose ISIN isn't known yet
  (Angel One files, brief 0002), until it is mapped (D7).
"""

V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE profile (
  id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE account (
  id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  broker TEXT NOT NULL, label TEXT NOT NULL,
  UNIQUE (profile_id, broker, label)
);

CREATE TABLE instrument (
  id INTEGER PRIMARY KEY,
  isin TEXT UNIQUE,
  contract_symbol TEXT UNIQUE,
  name TEXT, kind TEXT NOT NULL CHECK (kind IN ('EQUITY','ETF','MF','FNO')),
  CHECK ((isin IS NULL) <> (contract_symbol IS NULL))
);

CREATE TABLE symbol_alias (
  broker TEXT NOT NULL, raw_name TEXT NOT NULL,
  instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  source TEXT NOT NULL CHECK (source IN ('exact','user')), confirmed_at TEXT NOT NULL,
  PRIMARY KEY (broker, raw_name)
);

CREATE TABLE import_batch (
  id INTEGER PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  account_id INTEGER REFERENCES account(id),
  kind TEXT NOT NULL CHECK (kind IN
       ('tradebook','cas','opening','manual','taxpnl','ledger_statement')),
  broker TEXT, file_name TEXT, file_sha256 TEXT,
  imported_at TEXT NOT NULL, date_from TEXT, date_to TEXT,
  rows_read INTEGER, trades_added INTEGER, duplicates_skipped INTEGER,
  charges_total TEXT, file_charges_total TEXT, warnings_json TEXT,
  undone_at TEXT
);
-- A file is recognised as already imported only while its batch is live: after undo it can
-- be imported again.
CREATE UNIQUE INDEX batch_file ON import_batch (profile_id, file_sha256)
  WHERE undone_at IS NULL;

CREATE TABLE trade (
  id INTEGER PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  account_id INTEGER REFERENCES account(id),
  instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  source_id TEXT NOT NULL,
  raw_symbol TEXT,
  exchange TEXT, segment TEXT NOT NULL CHECK (segment IN ('EQUITY','FNO','MF')),
  trade_date TEXT NOT NULL, executed_at TEXT,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity TEXT NOT NULL, price TEXT NOT NULL,
  order_id TEXT, trade_ref TEXT,
  how_acquired TEXT,
  resolves_trade_id INTEGER REFERENCES trade(id),
  dedupe_key TEXT NOT NULL,
  UNIQUE (profile_id, dedupe_key)
);
CREATE INDEX trade_by_profile ON trade (profile_id, trade_date);

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

CREATE TABLE instrument_setting (
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  fund_class TEXT, fmv_2018 TEXT, name_for_112a TEXT,
  PRIMARY KEY (profile_id, instrument_id)
);

CREATE TABLE excluded_sale (
  trade_id INTEGER PRIMARY KEY REFERENCES trade(id) ON DELETE CASCADE,
  reason TEXT NOT NULL, excluded_at TEXT NOT NULL
);

CREATE TABLE brought_forward_loss (
  id INTEGER PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, year_arose INTEGER NOT NULL, amount TEXT NOT NULL
);

CREATE TABLE year_setting (
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  start_year INTEGER NOT NULL,
  residency TEXT NOT NULL DEFAULT 'RES' CHECK (residency IN ('RES','NOR','NRI')),
  return_filed_on_time INTEGER,
  itr_form TEXT, filed_at TEXT,
  PRIMARY KEY (profile_id, start_year)
);

CREATE TABLE year_snapshot (
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  start_year INTEGER NOT NULL,
  inputs_fingerprint TEXT NOT NULL, engine_version TEXT NOT NULL,
  computed_at TEXT NOT NULL, report_json TEXT NOT NULL, open_lots_json TEXT NOT NULL,
  PRIMARY KEY (profile_id, start_year)
);

CREATE TABLE statement_entry (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  posted_on TEXT NOT NULL, description TEXT NOT NULL,
  debit TEXT, credit TEXT, category TEXT, matched_on TEXT
);
"""

V2 = """
-- Task 3: settings kept in the ledger instead of page memory.
-- A fund class pre-filled from a CAS guess stays marked until the user confirms it.
ALTER TABLE instrument_setting ADD COLUMN fund_class_guessed INTEGER NOT NULL DEFAULT 0;
-- A hand-entered purchase names the sale it resolves by the sale's engine trade id. The V1
-- integer link to trade(id) has no ON DELETE action, so it would block undoing that sale's
-- import; it stays unused.
ALTER TABLE trade ADD COLUMN resolves_source_id TEXT;
"""

V3 = """
-- Brief 0003: FIFO per demat account, and transfers between one's own accounts.
-- An opening lot moved in from another of the user's accounts: the date it entered this one.
ALTER TABLE trade ADD COLUMN entered_on TEXT;

CREATE TABLE transfer (
  id INTEGER PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  on_date TEXT NOT NULL,
  instrument_id INTEGER NOT NULL REFERENCES instrument(id),
  quantity TEXT NOT NULL,
  from_account_id INTEGER NOT NULL REFERENCES account(id),
  to_account_id INTEGER NOT NULL REFERENCES account(id),
  created_at TEXT NOT NULL,
  CHECK (from_account_id <> to_account_id)
);

-- Trades saved before accounts existed: one account per broker, named after it.
INSERT INTO account (profile_id, broker, label)
  SELECT DISTINCT profile_id, broker,
         CASE broker WHEN 'zerodha' THEN 'Zerodha' WHEN 'upstox' THEN 'Upstox'
                     WHEN 'angelone' THEN 'Angel One' ELSE broker END
  FROM import_batch
  WHERE kind = 'tradebook' AND broker IS NOT NULL AND broker <> '' AND undone_at IS NULL;
UPDATE import_batch SET account_id = (
    SELECT a.id FROM account a
    WHERE a.profile_id = import_batch.profile_id AND a.broker = import_batch.broker)
  WHERE kind = 'tradebook';
UPDATE trade SET account_id = (SELECT b.account_id FROM import_batch b WHERE b.id = trade.batch_id)
  WHERE segment <> 'MF';
-- Opening holdings and hand-entered purchases go to the person's only account, if there is
-- exactly one; otherwise they stay unassigned and the app asks.
UPDATE trade SET account_id = (SELECT a.id FROM account a WHERE a.profile_id = trade.profile_id)
  WHERE account_id IS NULL AND segment <> 'MF'
    AND (SELECT COUNT(*) FROM account a WHERE a.profile_id = trade.profile_id) = 1;
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, V1), (2, V2), (3, V3))
"""(schema version, SQL) in order. The latest version is the last entry's."""

LATEST = MIGRATIONS[-1][0]
