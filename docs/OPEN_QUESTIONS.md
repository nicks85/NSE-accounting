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
- **Needed:** CBDT circular / ruling or CA confirmation.
- **Status:** open.

## Q-005 — Bonus stripping not implemented

- **Area:** corporate actions / capital-gains computation.
- **Rule (verified, 2025 Act s.175(9),(10)):** if securities or units are bought within 3
  months before the record date, bonus securities/units are allotted on them, and the original
  ones are sold within 9 months after the record date while the bonus ones are kept, the loss on
  the original ones is ignored and becomes the cost of the bonus ones still held. 1961 Act
  equivalent s.94(8) (extension to securities by Finance Act 2022 not yet checked against an
  official 1961 text, see Q-001).
- **Implemented as:** not implemented. Losses on such sales would be overstated.
- **Status:** open; must be implemented before the capital-gains computation ships.

## Q-006 — 31-Jan-2018 FMV after a split or bonus

- **Area:** grandfathering (s.112A / s.55(2)(ac)), to be built in Phase 1.
- **Problem:** the published 31-Jan-2018 FMV is per pre-split share. After a later split the
  per-share FMV for grandfathering must be divided by the split ratio, otherwise the
  grandfathered cost is overstated. Bonus shares allotted before 1-Feb-2018 also need an FMV.
- **Needed:** track a cumulative split factor per lot (or per-share FMV on the lot) when
  grandfathering is implemented; confirm treatment with a citation.
- **Status:** open.

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

- **Area:** carry-forward ledger.
- **Rule:** 1961 Act s.80 — losses under s.72, s.73, s.74 carry forward only if the return of
  loss was filed by the due date. The 2025 Act equivalent was not located in
  `docs/sources/` yet (s.263-related provisions mention returns of loss).
- **Implemented as:** carried-forward losses are listed with a warning; the engine can't know
  whether returns were filed on time.
- **Status:** open (2025 citation needed).

## Q-012 — Scope of the tax figure

- **Area:** `engine/api.py` (`special_rate_tax`).
- **Implemented as:** tax at the special rates on capital gains only. Not applied: surcharge,
  health and education cess, rebate, and the adjustment where other income is below the basic
  exemption limit (2025 Act s.196(2), s.198(3); 1961 Act s.111A(1) proviso, s.112A(3)).
  Business income is reported but taxed at slab rates outside Kosh.
- **Status:** open (scope decision for later phases).

## Q-013 — Holding-period boundary

- **Area:** `engine/classify/capital_gains.py`.
- **Best guess implemented:** a listed share is long-term only if sold *after* the same calendar
  date 12 months later (bought 10-Jan-2023: sold 10-Jan-2024 is short-term, 11-Jan-2024 is
  long-term). Dates that don't exist are clamped (29-Feb → 28-Feb).
- **Basis:** s.2(101)(a),(b) "held for not more than twelve months immediately preceding the
  date of its transfer"; exact day-counting convention not stated.
- **Status:** open.
