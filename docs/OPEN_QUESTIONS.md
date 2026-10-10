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
- **Best guess implemented:** losses are set off against slab-rate gains first (treated as the
  highest rate, up to 30%+), then the highest special rate; at equal rates, gains outside the
  ₹1.25 lakh exemption before exemption-eligible LTCG. Short-term losses go against STCG
  before LTCG; brought-forward losses oldest first, and LTCL before STCL. Losses are not netted
  inside their own bucket first, so the order applies to them too.
- **Caveat:** if the taxpayer's slab rate is below 20% (e.g. income covered by the rebate),
  setting losses against slab-rate gains first can increase tax.
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
- **Implemented as:** the user answers, per tax year, whether that year's return was filed by
  the due date (Losses screen, saved in the ledger; brief 0001 task 4).
  - **Filed late:** losses from that year are not set off in later years, with a notice. The
    year's own losses are flagged as not carrying forward.
  - **Not answered:** carried-forward losses are listed with the UNVERIFIED warning, as
    before.
- **Not handled:** condonation of delay (2025 Act / 1961 Act s.119(2)(b)). A user whose
  delay was condoned is told to mark that return as filed on time.
- **Status:** partly handled; the rule itself still needs CA confirmation (UNVERIFIED).

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
- **Implemented as:** no built-in Groww layout (CLAUDE.md forbids fabricating formats). A
  column-mapping importer takes the user's header names. XLSX (including PAN-protected Groww
  files) is read directly. **Angel One** now has a built-in importer
  (`importers/angel_one.py`), built from a real export; see Q-030.
- **Needed:** an anonymised Groww export to add a built-in profile.
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
  row error rather than being skipped. The first visible sheet is read by default (hidden
  sheets skipped with a warning); Excel error cells and formulas without a saved value are
  warned about. Integer cells are kept digit-for-digit; other numbers are rounded to 15
  significant digits.
- **Needed:** a real protected Groww XLSX and an XLSX export from Zerodha or Upstox.
- **Status:** open.

## Q-019 — Mutual fund classification

- **Area:** `engine/classify/funds.py`, `compute_tax_year(fund_classes=...)`.
- **Implemented as:** each fund's class (equity-oriented / specified / other) is supplied per
  ISIN. Without a class a fund is treated as "other" and flagged. Importers may pre-fill a class
  from the scheme name; that guess is flagged UNVERIFIED and should be confirmed by the user.
- **Law:** equity-oriented fund — 2025 Act s.198(8) (65% in domestic listed equity; 90%/90% for
  a fund of funds). Specified fund — 2025 Act s.76(5)(b): more than 65% in debt and money
  market instruments. The 1961 Act definition (s.50AA) was "not more than 35% in equity shares
  of domestic companies" for FY 2023-24 and FY 2024-25 and was changed to the 65%-debt test from
  FY 2025-26 (Finance (No. 2) Act 2024); the class supplied must match the year (not yet
  checked against an official 1961 text). `compute_tax_years(fund_classes_by_year=...)` lets
  the class differ by year; a "specified" class used for a year before FY 2025-26 is flagged.
  `unclassified_funds()` lists funds still needing a class. casparser's database only knows
  EQUITY and DEBT and labels overseas funds of funds, gold ETFs/FoFs, conservative hybrids and
  multi-asset funds EQUITY — none of which is equity-oriented — so every suggested class is
  warned and must be confirmed.
- **Status:** open (data input + 1961 text).

## Q-020 — FIFO for fund units: per folio or per scheme

- **Area:** CAS importer (instrument = ISIN#FOLIO).
- **Best guess implemented:** units are matched first-in-first-out within each folio. s.67(7)(c)
  prescribes FIFO for securities held in demat form; for statement-held (non-demat) units the Act
  is silent and practice is FIFO within the folio.
- **Status:** open.

## Q-021 — Non-equity fund redemptions before 23-Jul-2024

- **Area:** `engine/classify/capital_gains.py` (`manual` lines).
- **Implemented as:** before 23-Jul-2024 such units needed 36 months for long-term and LTCG was
  taxed at 20% with indexation (cost inflation index). Redemptions held up to 36 months are
  short-term at slab rates as usual; those held longer are flagged and excluded from the totals
  rather than guess the index values.
- **Needed:** official CII notification values to implement it (FY 2024-25 only).
- **Status:** open.

## Q-023 — Corporate actions on fund units

- **Area:** `engine/matching/fifo.py` (`apply_action`).
- **Implemented as:** splits, bonus units and scheme mergers/consolidations on fund ISINs are
  ignored with a warning. A later redemption can then fail as an oversell or give a wrong gain.
  Scheme consolidation keeps the original holding period (2025 Act s.2(101)(c)(B)(VII)) and
  isn't a transfer; CAS scheme mergers are imported as sale + purchase (Q-022).
- **Status:** open.

## Q-024 — Exchange-traded non-equity fund units (gold, debt, international ETFs)

- **Area:** `engine/classify/capital_gains.py`.
- **Implemented as:** any instrument whose ISIN starts with INF (fund units) goes through the
  fund rules even when bought on an exchange; without a class it is treated as "other" and
  flagged. Listed non-equity units use the 24-month period like unlisted ones.
- **Problem:** 1961 Act s.2(42A) (after Finance (No. 2) Act 2024) gives 12 months only to
  listed securities "other than a unit"; the 2025 Act s.2(101)(b)(i) says "security listed"
  without that carve-out. Whether listed units get 12 or 24 months under the 2025 Act needs
  confirmation.
- **Status:** open.

## Q-022 — CAS PDF → trades mapping unconfirmed

- **Area:** `importers/cas.py`.
- **Implemented as:** casparser parses the PDF; Kosh maps its transactions: purchases, SIPs,
  switch-ins and dividend reinvestments become buys at NAV, each followed stamp-duty row added
  to that purchase's cost; redemptions and switch-outs become sells, each following STT row
  recorded on that sale. A reversal cancels the latest earlier purchase with the same units and
  NAV. Dividend payouts and TDS are summed per year as notes (income from other sources, not
  handled). Gifts, segregated portfolios, unknown transaction types, a scheme that opens with
  units (cost unknown: a CAS from inception is needed), the same ISIN twice in a folio, and
  statements where casparser reports parse problems are refused.
- **Cost basis:** units x NAV (plus stamp duty), not the printed amount: the CAS amount column
  may already include stamp duty, which would then be counted twice. May differ from the amount
  paid by a few paise.
- **Mergers:** a scheme merger/consolidation isn't a transfer (2025 Act s.70(1)(zj), (zk);
  1961 Act s.47(xviii), (xix)); cost and holding period carry over (2025 Act
  s.2(101)(c)(B)(VII), (IX); 1961 Act s.49(2AD), s.2(42A) Expl. 1(hf) — 1961 numbers not
  checked against an official text). Kosh can't carry lots across schemes yet, so statements
  with mergers are refused unless the user allows them to be imported as a sale and purchase
  (gain overstated, warned).
- **Needed:** an anonymised real detailed CAS (CAMS and KFintech) to confirm the mapping.
- **Status:** open.


## Q-025 — ITR export: scope and line placement

- **Area:** `engine/export/itr.py`.
- **Implemented as:** Kosh fills Schedule 112A and Schedule CG of the official AY 2026-27
  ITR-2 / ITR-3 JSON (schemas in `engine/export/schemas/`), validated against the schema. It
  does not produce a complete return: personal details, other heads of income, Schedules
  CYLA/BFLA/CFL, Schedule BP (ITR-3 business income), Schedule SI and the tax computation must
  be completed in the official utility.
- **Best guesses:** s.111A gains go to CG A2 (section code 1A); non-equity fund short-term gains
  to A5 "other assets"; s.112 LTCG on fund units to B "assets not covered elsewhere";
  Schedule 112A uses one row per lot for holdings acquired on or before 31-Jan-2018 (so each
  row's "higher of cost and lower of sale value and FMV" holds; the same ISIN may appear on
  several rows) and a single CONSOLIDATED row (no quantity or per-share price) for later ones,
  with the ISIN used as the name when none is given; per-share figures are derived from the
  whole-rupee totals to 4 decimals; Schedule CG items C and E are floored at 0 (schema minimum),
  losses being carried in the set-off table;
  TotalBalance112A (ITR-2) = Balance112A; amounts are rounded to whole rupees per line.
  The quarter-wise accrual table splits each column's gain left after current-year set-off in
  proportion to that column's net gain per period. Schema-valid JSON may still fail the
  utility's own business-rule checks.
- **Needed:** a test upload into the official ITR utility; CA review of line placement.
- **Status:** open.

## Q-026 — FIFO per demat account, and shares moved between one's own accounts

- **Area:** `engine/matching/fifo.py`; decision briefs 0001 (D4) and 0003.
- **Rule cited:** FIFO for securities held in demat form (1961 Act s.45(2A); 2025 Act
  s.67(7)(c)).
- **Found:** CBDT Circular 768 (24-Jun-1998), read through a reproduction on taxguru.in. The
  incometaxindia.gov.in page refused automated access, so it still needs checking against the
  official copy. Two passages:
  - "FIFO method will be applied accountwise. This is because in case where a particular
    account of an investor is debited for sale of securities, the securities lying in his
    other account cannot be construed to have been sold"
  - "under the FIFO method, the basis for determining the movement out of the account is the
    date of entry into the account"
- **Implemented (brief 0003, approved 2026-10-10):**
  - FIFO runs per demat account.
  - Same-day intraday netting happens within one account.
  - Shares moved between one's own accounts leave the source FIFO, and queue in the
    destination by the date they arrived.
  - **They keep their purchase date and cost.** This is a reading of s.2(47): the owner doesn't
    change, so it isn't a transfer.
  - **Tax totals stay per person**, as in one return per PAN.
- **Still to verify (`UNVERIFIED`):**
  - Does Circular 768 apply under the 2025 Act (s.67(7)(c))?
  - Do moved shares keep their original holding period and cost? The circular doesn't say;
    the 2025 Act definition of "transfer" is still to be found.
  - Moves to a family member's account are gifts, not covered here (see Q-029).
- **Status:** partly verified (1961 Act, per-account FIFO); the rest is open.

## Q-027 — Which charges form part of cost and transfer expenses

- **Area:** `engine/models.py` (`Trade.charges`); decision brief 0001 (D8).
- **Implemented today:** all non-STT charges on a trade (brokerage, GST, exchange turnover,
  SEBI fee, stamp duty) are added to cost on buys and treated as transfer expenses on sells
  (1961 s.48(i),(ii); 2025 s.72(1)). STT is excluded (1961 s.48 proviso; 2025 s.72(3)(b)).
- **Source found (brief 0005, 2026-10-10):** the Income Tax Department's capital gains
  guidance, read through search-result excerpts because incometaxindia.gov.in refused
  automated access. It needs checking against the page itself.
  - "it is reasonable to include in the actual cost of a capital asset all the expenses which
    are incurred by the assessee to acquire it"
  - "the brokerage or commission, stamp duty, registration fee, traveling expenses, legal
    expenses, etc., incurred in connection with the transfer are allowed to be deducted"
- **Shown since brief 0005:** charges are kept by type where the file gives them (Angel One;
  CAS stamp duty). They are shown in each gain's "why?" and in the PDF's "Charges by type"
  table. Tax still uses each trade's total.
- **Open points:**
  1. GST on brokerage and stamp duty on the buyer: part of the cost of acquisition? (Best
     guess: yes. Stamp duty and brokerage are named in the guidance above; GST, levied on
     brokerage and exchange fees, is not.)
  2. DP charges, debited per scrip on sell days and visible only in the ledger statement:
     transfer expenses of that sale? (Best guess: yes, but only if the user applies them from
     the ledger statement; `UNVERIFIED`.)
  3. For intraday and F&O (business income), all charges including STT as deductible business
     expenses — STT under 1961 s.36(1)(xv) / 2025 s.32(k) is cited; the other charges are
     assumed deductible under the general business-expenditure provision (section not yet
     cited).
- **Status:** open.

## Q-028 — What residency status changes

- **Area:** a per-year setting (brief 0006, approved 2026-10-10).
- **Cited from the 2025 Act text in `docs/sources/` (checked 2026-10-10):**
  1. **The shortfall is for residents only.** The basic-exemption shortfall reduces
     special-rate gains only for "an individual or a Hindu undivided family, being a
     resident" (s.196(2), s.197(2), s.198(3)). 1961 Act: s.111A(1) proviso, s.112(1) proviso,
     s.112A(2), still to check against an official text (Q-001).
  2. **So is the rebate.** It is for "an individual resident in India" (s.156; 1961 s.87A),
     and it isn't allowed against the s.198 tax (s.198(7)).
  3. **The rates and exemption have no residency condition:** 20% (s.196(1)), and 12.5% above
     ₹1,25,000 (s.198(2)). They apply equally to non-residents.
- **Implemented:** notices only. Kosh applies neither the shortfall nor the rebate, so the
  figure is right for a non-resident and can only overstate a resident's tax.
  4. **TDS on a non-resident's gains:** s.393(2), Table Sl. No. 17. This covers "any other sum
     chargeable" paid to "any non-resident (not being a company)", at rates in force (1961
     s.195).
  5. **RNOR is a resident for the shortfall.** "Non-resident" includes a person not ordinarily
     resident (s.6(13)) only for ss.161, 174 and 312 (s.2(72)). 1961 Act: s.6(6).
- **Still open:**
  - The 1961 Act sections, against an official text (Q-001).
  - Whether 1961 s.112A(6) is the counterpart of s.198(7), which disallows the rebate against
    equity LTCG.
  - Whether 1961 s.2(30) is the counterpart of 2025 s.2(72) for RNOR ("non-resident" defined).
- **Status:** verified against the 2025 Act; the 1961 sections are still to be checked.

## Q-029 — Cost and acquisition date for holdings not bought on the exchange

- **Area:** opening holdings and the "missing purchase history" form; decision brief 0001
  (D5).
- **Believed (1961 Act; 2025 Act sections not yet found; all to verify):**
  - IPO allotment: cost = allotment price; date = allotment date.
  - Bonus shares: cost nil (s.55(2)(aa)(iiia)); date = allotment date.
  - Gift or inheritance: cost = previous owner's cost (s.49(1)); holding period includes the
    previous owner's (s.2(42A) Explanation 1(b)); grandfathering applies by the previous
    owner's acquisition date (to verify).
  - ESOP shares: cost = value taxed as a perquisite on exercise (s.49(2AA)); date = exercise
    or allotment date (to verify which).
  - Transfer between one's own demat accounts: not a transfer; original date and cost kept
    (see Q-026).
- **Proposed:** the form asks "how acquired" and applies the matching rule, marked
  `UNVERIFIED` until each item is cited.
- **Also assumed by opening holdings (brief 0001 task 7, to verify):**
  - A gifted or inherited lot uses the previous owner's purchase date. That date also
    decides its place in FIFO order among the user's own purchases of the same share.
  - For ESOP shares, the form doesn't settle between the exercise date and the allotment
    date; the user enters the one they believe applies.
  - Fractional share quantities are accepted, with a warning. They can arise from corporate
    actions.
  - A lot with the same ISIN, date, quantity and price as one already saved is treated as the
    same lot.
- **Status:** open.

## Q-030 — Angel One "TradesAndCharges" layout

- **Area:** planned built-in Angel One profile (`importers/`); extends Q-017.
- **Known from a real header supplied by the user (file not committed):** sheet
  `TradesAndCharges`; a "TradeBook And Charges" title above the header row; columns
  Scrip/Contract, Buy/Sell, Buy Price, Sell Price, Quantity, Brokerage, GST, STT, Sebi Tax,
  Exchange Turnover Charges, Stamp Duty, Other Charges, IPFT Charges, Order Type, Segment,
  Exchange, Order ID, Trade ID, Date. Order Type "Delivery" with Segment "CAPITAL" = cash
  delivery. The price is in Buy Price for buys and Sell Price for sells. No ISIN column.
- **Confirmed from the user's own file (2026-10-07; file not committed):** date is
  `YYYY-MM-DD` with no time; one row per trade (fill), several rows per Order ID; the "Charges
  Summary" block (Total Trades = orders, Total Charges, Total Trade Charges, Total Non Trade
  Charges) and per-kind breakdowns sit above the "TradeBook And Charges" title; the sum of
  per-row charges matches the summary within ₹0.10 (the summary is rounded).
- **Still unknown:** how F&O contracts are written in Scrip/Contract; the Order Type and
  Segment values for intraday and F&O.
- **Implemented as (2026-10-07):** `importers/angel_one.py` imports Segment `CAPITAL` rows
  (any Order Type other than "Delivery" is imported with a warning, and same-day round trips
  are treated as intraday by the classifier); other segments are skipped with a warning.
  Without an ISIN column, scrips are keyed `NAME:<scrip name>` (brief 0002); names that look
  like an ETF or fund get a warning. Truncation to about 20 characters means two companies
  with the same shortened name would be treated as one (not seen in practice; the bundled
  company list of brief 0001 task 11 resolves it). The per-row charges
  are compared with "Total Trade Charges" (₹1 tolerance), and non-trade charges such as DP
  charges are listed but not deducted (Q-027).
- **Status:** open (needs an anonymised excerpt).

## Q-031 — Reporting when a sale is excluded for missing purchase history

- **Area:** decision brief 0001 (D5).
- **Problem:** if the user excludes a sale whose purchase cannot be found, the gains for the
  year are understated. The law has no "excluded" concept; the sale still has to be reported
  with some cost.
- **Proposed:** never assume a zero or guessed cost. Withhold the year's total while sales
  are unresolved; on explicit exclusion, label every total, the PDF and the export as
  "excludes N sales (₹X sale value)".
- **Needed:** CA view on what a filer should do when cost records are genuinely lost.
- **Status:** open.

## Q-032 — Company names to ISINs: data sources and licences

- **Area:** brief 0007 (replaces brief 0001 D7).
- **Exchanges (checked 2026-10-10):**
  - NSE's terms of use say its data "shall not be copied, … reproduced, … stored … distributed
    in any form, without prior written permission of NSE".
  - BSE's disclaimer says the same.
  - So Kosh can't bundle a list built from their files.
- **`casparser-isin`** (a dependency, shipped inside the engine for CAS imports) has a table of
  260,103 ISINs, 107,155 of them active equity shares. The package is MIT, but the source and
  licence of its data aren't stated.
  - **Not used for name matching** (brief 0007, option a only).
  - **Still open:** whether Kosh should keep shipping that table at all.
- **Implemented:** the user types each name's ISIN once (check digit verified). It is remembered
  per broker and name (`symbol_alias`), and saved trades move to the ISIN.
- **Status:** open (the licence of the shipped table).

## Q-033 — Rights entitlements (RE) and "Adjustment" lots

- **Area:** planned Angel One importers; decision brief 0001.
- **Observed:** the Angel One Tax P&L lists rights-entitlement ISINs (`…-RE`) with zero buy
  and sell values (entitlements that lapsed), and "Adjustment" lots with zero values.
- **Unknown:**
  - the tax treatment of a renounced or lapsed rights entitlement, and the cost of shares
    acquired by exercising one (1961 s.55(2)(aa); 2025 Act section not yet found);
  - what Angel One's "Adjustment" represents.
- **Proposed:** import such rows as information only, listed with a warning, until the rule is
  cited.
- **Status:** open.

## Q-034 — Share buybacks from 1-Oct-2024

- **Area:** classification; decision brief 0001.
- **Observed:** from FY 2025-26 the Angel One Tax P&L reports delivery P&L "for Buyback"
  separately.
- **Believed (to verify):** from 1-Oct-2024 the buyback amount is taxed as a dividend in the
  shareholder's hands. The cost of the shares bought back is allowed as a capital loss. The
  1961 Act provisions are s.2(22)(f) and s.46A as amended by Finance (No. 2) Act 2024; the
  2025 Act sections are not yet found.
- **Proposed:** detect buyback sales (tender-offer rows) and flag them. Do not treat them as
  ordinary sales until the rule is implemented with citations.
- **Status:** open.

## Q-035 — ETFs imported without an ISIN

- **Area:** `importers/angel_one.py` (`FUND_LIKE`); decision brief 0002.
- **Problem:** Kosh tells an ETF or fund from a share by its ISIN (`INF…`). A broker file with
  no ISIN (Angel One trade history) imports every scrip as `NAME:<scrip>`, which the engine
  treats as a listed share: equity rates and the ₹1.25 lakh exemption. That is wrong for gold,
  debt, liquid and international ETFs (Q-024), and only right for equity ETFs if they meet the
  equity-oriented test.
- **Implemented as:** a warning, not a guess. Names containing ETF, BEES, FUND, GOLD, SILVER,
  LIQUID, MON100 or MAFANG anywhere are flagged; the figures are not changed.
- **Resolves when:** the bundled company list (brief 0001 task 11) supplies the ISIN, after
  which the existing fund-class question on the Gains screen applies.
- **Status:** open.

## Q-036 — A CAS reversal of a purchase saved from an older CAS

- **Status:** open, not handled (logged 2026-10-10 from the task 2 review).
- **What happens:** the CAS importer cancels a reversed purchase only within the same
  statement (`importers/cas.py`, `_cancel_purchase`). If an older CAS was imported first and
  its purchase is reversed in a later statement, the later file only carries the reversal.
  The purchase saved from the older file then stays in the ledger, with phantom units.
- **Workaround today:** undo the older CAS import and import the newer, longer CAS, which
  covers both the purchase and its reversal.
- **To decide:** whether a reversal with no matching purchase in the file should cancel a
  saved purchase with the same date, units and NAV, or stop the import and ask.
