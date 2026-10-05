# Open questions

Tax rules that are ambiguous, unverified, or have conflicting sources. Every rule shipped with
`UNVERIFIED = True` must have an entry here. Resolve with a citation, then remove the flag.

## Q-001 — Section citations: 2025 Act verified, 1961 Act partly verified

- **2025 Act:** verified against the official PDF in `docs/sources/` (human-downloaded
  5-Oct-2026, since the site returns 403 to automated fetches). The table of verified
  sections is in `docs/sources/README.md`.
- **1961 Act:** 111A/112A rates, the 23-Jul-2024 cutover and the ₹1.25 lakh limit are
  confirmed by the CBDT FAQ (`docs/sources/`). Other 1961 section numbers (2(42A), 43(5),
  45(2A), 55(2)(aa)(iiia), 55(2)(ac), 55(2)(b)(v), 70-74, 94(8)) are the well-known numbers
  but have not been checked against an official 1961 text in this repo.
- **Needed:** an official copy of the Income-tax Act 1961 as amended by Finance (No.2) Act
  2024 / Finance Act 2025, for FY 2024-25 and FY 2025-26 rule packs.
- **Status:** partly resolved (2025 Act done; 1961 Act pending).

## Q-002 — Holding period after a share split (sub-division)

- **Area:** `engine/matching/corporate_actions.py` (`Split`, flagged `UNVERIFIED_SPLIT`).
- **Resolved:** cost. The cost of shares from a sub-division or consolidation is computed with
  reference to the cost of the original shares: 2025 Act s.90(9)(d)(i),(iv); 1961 Act
  s.55(2)(b)(v).
- **Open:** holding period. The engine carries over the original acquisition date. The 2025
  Act's list of periods to include (s.2(101)(c)(B)) does not mention sub-division or
  consolidation, so this rests on practice and on FIFO under s.67(7)(c), not an explicit
  provision.
- **Also:** cash paid for fractional entitlements on a consolidation is a transfer, so it
  should give rise to a capital gain on the fraction. Not yet modelled; the engine warns.
- **Needed:** CA confirmation (or a ruling/circular) on the holding period.
- **Status:** open (holding period only).

## Q-003 — Getting the bonus allotment date into the engine

- **Area:** `engine/matching/corporate_actions.py` (`Bonus.allotment_date`), importers.
- **Resolved:** the holding period of bonus shares runs from the date of allotment —
  2025 Act s.2(101)(c)(C)(IV).
- **Open:** when the allotment date isn't supplied the engine falls back to the ex-date, which
  can be a day or two early and could move a sale across the 12-month boundary. Importers
  need a source for the allotment date (broker corporate-action feed, CAS, or user input).
- **Also:** entitlement assumes holdings at the start of the ex-date. Under T+1 settlement the
  ex-date and record date coincide, so a buy on the ex-date is not entitled.
- **Status:** open (data source only).

## Q-004 — Intraday netting convention for cash equity

- **Area:** `engine/classify/trades.py` (flagged `UNVERIFIED_INTRADAY_NETTING`).
- **Implemented as:** for each scrip and day, the smaller of total bought and total sold is
  intraday (speculative, s.43(5)); the rest is delivery. Intraday units are taken from the
  earliest trades of the day on each side. This applies even when the scrip is also held from
  earlier (e.g. hold 100, buy 50 and sell 50 today → 50 intraday, holding unchanged).
- **Problem:** s.43(5) defines a speculative transaction as one settled without delivery, but
  doesn't say how to allocate units within a day. Brokers' Tax P&L reports use this netting,
  and exchange settlement nets the same way, but no CBDT citation has been found.
- **Also:** scrips are identified by ISIN, so a BSE buy and an NSE sell of the same ISIN on the
  same day are netted as intraday too.
- **Needed:** CBDT circular / ruling or CA confirmation.
- **Status:** open.

## Q-005 — Bonus stripping: 1961 Act text

- **Area:** `engine/matching/fifo.py` (`FifoBook._strip_bonus`).
- **Implemented:** 2025 Act s.175(9),(10), verified against `docs/sources/`: a loss on
  securities bought within 3 months before the record date and sold within 9 months after it is
  ignored if bonus securities are still held after the sale, and becomes the cost of those
  bonus securities.
- **Open:** for FY 2024-25 and FY 2025-26 the same rule is applied under 1961 Act s.94(8).
  That Finance Act 2022 extended s.94(8) from units to securities is not yet checked against an
  official 1961 text, so it's flagged UNVERIFIED in those years.
- **Status:** open (1961 text only).

## Q-006 — 31-Jan-2018 FMV after a split or bonus

- **Area:** `engine/classify/capital_gains.py`, `Lot.split_factor`.
- **Best guess implemented:** each lot tracks the product of split/consolidation ratios with
  ex-dates after 31-Jan-2018; the published per-share FMV is divided by it. Splits on or before
  that date don't change it (the published FMV is already post-split). Bonus shares allotted
  before 1-Feb-2018 use the same per-share FMV. Flagged in output whenever the factor isn't 1.
- **Needed:** CA confirmation (s.90(8)(b) / s.55(2)(ac) define FMV per asset, silent on splits).
- **Status:** open (best guess, flagged in output).

## Q-007 — FY 2024-25: which LTCG portion gets the ₹1.25 lakh exemption

- **Area:** `engine/rules/fy2024_25.py`, `engine/rules/setoff.py` step 5.
- **Best guess implemented:** the exemption is applied to LTCG taxed at 12.5% (transfers on or
  after 23-Jul-2024) before LTCG taxed at 10%. This minimises tax.
- **Known:** the CBDT FAQ (Q7) confirms ₹1.25 lakh applies to the whole of FY 2024-25 but does
  not say how to allocate it between the two rates.
- **Needed:** CA confirmation, or the ITR-2 AY 2025-26 Schedule 112A/CG instructions.
- **Status:** open (best guess, flagged in output).

## Q-008 — Order of set-off between rate buckets

- **Area:** `engine/rules/setoff.py`.
- **Best guess implemented:** losses are set off against the highest-rate gains first;
  short-term losses go against STCG before LTCG; brought-forward losses oldest first, and LTCL
  before STCL.
- **Problem:** the Act says which gains a loss may be set off against, not the order between
  eligible buckets. The ITR utility may apply its own order.
- **Status:** open (best guess, flagged in output).

## Q-009 — STT-paid condition for the special equity rates

- **Area:** `engine/classify/capital_gains.py`.
- **Best guess implemented:** every delivery disposal of listed equity is assumed to meet the
  STT conditions of s.196 / s.198 (s.111A / s.112A), so the 20% / 12.5% rates apply.
- **Problem:** off-market transfers, and acquisitions without STT (IPO, bonus, ESOP, etc.,
  which have notified exceptions), are not checked.
- **Status:** open (best guess, flagged in output).

## Q-010 — F&O loss against capital gains in the same year

- **Area:** `engine/rules/setoff.py` step 3.
- **Best guess implemented:** a current-year F&O loss not absorbed by speculative income is set
  off against capital gains (highest rate first) under s.109 / s.71; the rest is carried
  forward.
- **Problem:** inter-head set-off is mandatory against the taxpayer's other heads too (house
  property, other sources; not salary), which Kosh doesn't see. The result may differ from the
  final return.
- **Status:** open (best guess, flagged in output).

## Q-011 — Carry forward requires a timely return

- **Rule:** no loss carries forward unless determined in a return filed under s.263(1) —
  2025 Act s.121 (verified in `docs/sources/`); 1961 Act s.80 (due date, s.139(3)).
- **Implemented as:** carried-forward losses are listed with a warning; the engine can't know
  whether returns were filed on time.
- **Status:** open (data, not law: needs a user input per year).

## Q-012 — Scope of the tax figure

- **Area:** `engine/api.py` (`special_rate_tax`).
- **Implemented as:** tax at the special rates on capital gains only. Not applied: surcharge,
  health and education cess, rebate, and the adjustment where other income is below the basic
  exemption limit (2025 Act s.196(2), s.198(3); 1961 Act s.111A(1) proviso, s.112A(3)).
  Business income is reported but taxed at slab rates outside Kosh. Total income is also
  rounded to ₹10 before tax (s.516 / s.288A); the engine taxes unrounded gains, so its figure
  can differ from the return by about ₹10.
- **Status:** open (scope decision for later phases).

## Q-013 — Holding-period boundary

- **Area:** `engine/classify/capital_gains.py`.
- **Best guess implemented:** a listed share is long-term only if sold *after* the same calendar
  date 12 months later (bought 10-Jan-2023: sold 10-Jan-2024 is short-term, 11-Jan-2024 is
  long-term). Dates that don't exist are clamped (29-Feb → 28-Feb).
- **Basis:** s.2(101)(a),(b) "held for not more than twelve months immediately preceding the
  date of its transfer"; exact day-counting convention not stated.
- **Status:** open.

## Q-014 — Bonus-stripping window boundaries

- **Area:** `engine/matching/fifo.py` (`FifoBook._strip_bonus`).
- **Best guess implemented:** "within three months before the record date" = bought on or
  after the same date 3 months earlier and before the record date; "within nine months after" =
  sold after the record date and on or before the same date 9 months later. Record date
  defaults to the ex-date (T+1). Shares bought on or after the ex-date are not entitled, so
  they are excluded. The bonus shares must be allotted on or before the sale date to count as
  "held". The loss is worked out per FIFO lot, not netted across all clause-(a) securities.
  The ignored loss includes trade charges. If several bonus lots qualify, the first bonus (by
  ex-date) takes the loss. Bonus shares supplied only as opening lots can't be checked (warning).
- **Status:** open.

## Q-015 — Zerodha tradebook format unconfirmed

- **Area:** `importers/zerodha.py` (`FORMAT_CONFIRMED = False`).
- **Confirmed (Zerodha support docs):** Console → Reports → Tradebook, CSV or XLSX, max 365
  days per download; corporate actions, IPO/OFS, buybacks and inter-broker transfers are not
  included.
- **Unconfirmed:** column names (symbol, isin, trade_date, exchange, segment, series,
  trade_type, auction, quantity, price, trade_id, order_id, order_execution_time) come from
  third-party parsers; date formats, `trade_type` casing and segment codes (EQ / FO) are
  assumptions. Headers are matched by name, tolerant of case and spacing. XLSX not supported
  yet. Dates with "/" are read as day/month (warning when ambiguous). `auction` rows are
  imported as normal trades with a warning; `series` is ignored.
- **Also:** trade numbers are assumed unique per exchange per day (dedupe key exchange + date +
  trade_id). The tradebook has no charges or STT; trades import with zero charges, slightly
  overstating gains.
- **Needed:** an anonymised real tradebook export (one row per segment is enough).
- **Status:** open.

## Q-016 — Upstox tradebook format unconfirmed

- **Area:** `importers/upstox.py`.
- **Basis:** field names, date format (YYYY-mm-dd), BUY/SELL and segment codes (EQ, FO, CD,
  COM, MF) are documented for Upstox's trade-history API, not for the downloadable report
  (https://upstox.com/developer/api-documentation/get-historical-trades/). Upstox says the
  trade report downloads as Excel, CSV or PDF. Aliases "Date", "Side", "Trade Num",
  "Trade Time" come from secondary sources and rank below the documented names.
- **F&O identity:** the API's `symbol` is the underlying, so contracts are built as
  SYMBOL:EXPIRY:FUT or SYMBOL:EXPIRY:STRIKE:CE/PE from `expiry`, `strike_price`,
  `option_type`. If the real report has a full trading symbol instead, this needs revisiting.
- **Segment guess:** without a segment column, NSE/BSE rows are treated as equity and NFO/BFO
  as F&O (warned in output); mutual-fund or currency rows could be misread.
- **Needed:** an anonymised real Upstox tradebook export.
- **Status:** open.

## Q-017 — Groww and Angel One layouts not documented

- **Area:** `importers/mapped.py`.
- **Known:** Groww's order history downloads from Profile → Reports → Transactions as XLSX,
  password-protected with the PAN. Angel One's "trade history" (Account → Trades and charges)
  includes charges, downloadable as XLSX/XLS/CSV. Neither publishes column names.
- **Implemented as:** no built-in layout (CLAUDE.md forbids fabricating formats). A
  column-mapping importer takes the user's header names. XLSX (including PAN-protected Groww
  files) is read directly.
- **Needed:** anonymised real exports to add built-in profiles.
- **Status:** open.

## Q-018 — XLSX reading: unverified against real broker files

- **Area:** `importers/xlsx.py`.
- **Implemented as:** a minimal reader of the sheet XML (first sheet by default). Numbers are
  rounded to 15 significant digits, as Excel displays them; cells with date number formats
  become ISO dates. Password-protected files are decrypted locally with msoffcrypto-tool.
- **Known limitation:** msoffcrypto's own encrypt → decrypt round trip fails for encrypted
  packages under about 4 KB; such files get a clear "couldn't decrypt" error. Whether real
  Groww files hit this is unknown.
- **Also:** report footers (totals, disclaimers) below the trades make the import fail with a
  row error rather than being skipped.
- **Needed:** a real protected Groww XLSX and an XLSX export from Zerodha or Upstox.
- **Status:** open.
