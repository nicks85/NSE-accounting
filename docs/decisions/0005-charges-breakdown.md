# 0005 — Charges by type for each trade

- **Status:** approved 2026-10-10 as recommended (A, B1, C, D). No Zerodha/Upstox samples yet (task 14 waits for them).
- **Date:** 2026-10-10
- **Builds on:** brief 0001 D8 (task 10) and Q-027
- **Scope:** importers (Angel One, CAS), ledger (the `trade_charge` table already exists), RPC,
  the Gains "Why?" view and the PDF summary

## Problem

Kosh keeps one charges total per trade, plus STT. You can't see what that total is made of:
brokerage, GST, exchange fees, SEBI fee, stamp duty and so on. So you can't check it against a
contract note, and a CA can't see which charges were treated as cost.

## What files give today

| Source | Breakdown in the file | What Kosh keeps today |
|---|---|---|
| Angel One trade history | Brokerage, GST, STT, SEBI tax, exchange turnover, stamp duty, other, IPFT per row | One total, plus STT |
| Mutual fund CAS | Stamp duty on purchases, STT on redemptions | Stamp duty as charges, STT apart |
| Zerodha and Upstox tradebooks | **No charges at all** | Zero: gains are slightly overstated (the importers say so) |
| Opening holdings, hand-entered purchases | One "charges" figure | One total |

## Tax treatment (Q-027): no change, now with a source

The Income Tax Department's own guidance on capital gains says:

- cost of acquisition: "it is reasonable to include in the actual cost of a capital asset all
  the expenses which are incurred by the assessee to acquire it";
- transfer expenses: "the brokerage or commission, stamp duty, registration fee, traveling
  expenses, legal expenses, etc., incurred in connection with the transfer are allowed to be
  deducted".

This was read through search-result excerpts of incometaxindia.gov.in pages, because the site
refuses automated access. It needs a check against the page itself.

This matches what Kosh does:

- **Buys:** every charge except STT is added to cost (1961 s.48(ii); 2025 s.72(1)).
- **Sells:** every charge except STT is a transfer expense (1961 s.48(i)).
- **STT:** never deducted from capital gains. It is deductible from intraday and F&O income.

**Still a best guess (UNVERIFIED, Q-027):**

1. GST charged on brokerage and exchange fees. The guidance doesn't name it; it is treated as
   part of the charge it is levied on.
2. DP charges. They are debited per scrip on sell days but aren't on the trade rows, so they
   aren't applied. Angel One's note about "charges not tied to a trade" stays.
3. Other charges against intraday and F&O income. They are assumed deductible as business
   expenses; the section is not cited yet.

## Decisions

### A. What is stored

**Recommendation:** each trade's charges by type in the existing `trade_charge` table: brokerage,
GST, exchange, SEBI, stamp duty, IPFT, other and STT. This replaces today's single "other" row.

- **Matching and tax use the totals,** as today, so no figure changes.
- **Trades saved before this change** keep their single "other" figure. The screen shows "not
  broken down".

### B. Showing it on a gain line ("Why?")

| Option | For | Against |
|---|---|---|
| **1. Show the breakdown of the whole buy and sell trades, saying what share of each this line is** (for example "on the purchase of 100 shares; 40 of them are in this line") | Exact: these are the figures on the contract note | You do the proportion in your head |
| 2. Split each charge type in proportion to each line | Per line | Paisa rounding per type might not add up to the line's total, which is the figure actually used |

**Recommendation: 1.**

### C. The PDF summary

**Recommendation:** add a "Charges by type" table for the year: the sum of each type on the
year's trades, buys and sells shown apart. It is information only and changes no figure. It
also says which accounts' files carry no charges, Zerodha and Upstox today.

### D. Missing charges for Zerodha and Upstox

Their tradebooks have no charges. Getting them needs contract notes or the broker's Tax P&L.
That is task 14, and it needs anonymised sample files from you. Until then Kosh keeps warning
that gains are slightly overstated. **This brief doesn't change that.**

## Questions for you

1. Approve brief 0005 as recommended? That's A (store by type), B1, C (a PDF table) and D (no
   change for Zerodha and Upstox).
2. Do you have a Zerodha or Upstox contract note or Tax P&L you could share anonymised later?
   It's needed to fill in their charges (task 14).

## Open tax questions

Q-027 is updated with the source above. Points 1–3 stay UNVERIFIED.

## Implementation notes (2026-10-10)

- **`Trade.charge_parts`:** pairs of (kind from `CHARGE_KINDS`, amount) that must add up to
  `charges`.
  - It is excluded from trade equality, since it is information only.
  - A slice of a trade (`portion`, `split`) carries no breakdown.
- **Importers:**
  - Angel One fills it from its charge columns.
  - The CAS records a purchase's stamp duty as `STAMP`.
  - Zerodha and Upstox stay without charges.
- **Ledger:** each type is a `trade_charge` row. A lone `OTHER` reads back as "not broken
  down", which is how totals from before this change, and hand-entered charges, come back.
- **`api.charges_by_type(trades, year)`:** sums by type (on buys, on sells), plus STT and
  "Not broken down". It also counts trades with no charges at all, by source. The PDF summary
  gets a "Charges by type on this year's trades" table, with a line naming the sources whose
  files carry no charges.
- **The gain's "Why?"** lists the whole purchase's and sale's charges by type, and says how
  many of that trade's shares are in the line (B1). The ids of trade slices (`#delivery`) are
  traced back to the whole trade.

### Fixes from the QA review (2026-10-10)

- **Mutual-fund lines:** "why?" strips only the `#delivery` and `#intraday` slice suffixes. A
  fund's id contains `#` for its folio, so cutting at the first `#` found no trade.
- **"Files with no charges" in the PDF** lists broker tradebooks only. A fund redemption, a
  hand-entered or opening lot, or an Angel One row with no charges genuinely had none.
- **After a split or bonus,** when a line holds more shares than its purchase, "why?" says the
  shares come from that purchase after the action. It no longer says "20 of those 10".
- **Each charge by type must be positive.** Angel One's types are kept in the ledger's order,
  so a trade reads back exactly as imported.
- **Wording:**
  - Hand-entered and opening lots say "entered by hand" or "no charges entered".
  - Angel One trades saved before this change say how to get their breakdown: undo and import
    again.
  - The PDF note now covers intraday and F&O, and says GST and the business-income treatment
    are still to be confirmed.
- **The PDF table** has a totals row, and isn't drawn when only uncharged files exist.
- **Round 2:**
  - Each gain line now carries its `split_factor`, so after a split "why?" counts the purchase
    in today's shares.
  - The Angel One re-import hint is gone. Kosh can't tell a trade saved before this change from
    a row whose only charge is "Other charges", so it now says "not broken down by type".
