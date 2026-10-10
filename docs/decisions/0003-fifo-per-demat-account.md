# 0003 — FIFO per demat account, and transfers between your own accounts

- **Status:** approved 2026-10-10 as recommended (A1, B, C, D).
- **Date:** 2026-10-10
- **Builds on:** brief 0001 D4 and task 8 (your answer 2: "per demat account"), Q-026
- **Scope:**
  - engine: the matcher, classification, the ledger (schema v3) and the RPC
  - UI: the import, opening-holdings and missing-history forms, a new Transfers section, and
    the Holdings screen

## Problem

Today Kosh runs FIFO per share across every file you import. If you hold the same share at
two brokers, a sale at one is matched against purchases at the other. The cost and holding
period can then come out wrong.

## What the source says (new since brief 0001)

CBDT Circular 768 (24-Jun-1998) explains FIFO for demat holdings under 1961 Act s.45(2A).
Quoted from taxguru.in; the incometaxindia.gov.in copy refused automated access, so I still
need to check the official text.

> "FIFO method will be applied accountwise. This is because in case where a particular
> account of an investor is debited for sale of securities, the securities lying in his
> other account cannot be construed to have been sold"

> "under the FIFO method, the basis for determining the movement out of the account is the
> date of entry into the account"

Two consequences:

1. **FIFO runs per demat account.** This is now sourced for the 1961 Act. The 2025 Act
   counterpart is s.67(7)(c). Whether Circular 768 still applies under the 2025 Act is not
   known, so that part stays UNVERIFIED.
2. **Shares moved in from your own other account queue by the date they entered this
   account**, not by their original purchase date.
   - Their **cost and holding period** still come from the original purchase: moving
     between your own accounts isn't a transfer for capital gains, because the owner doesn't
     change (my reading of 1961 Act s.2(47); the 2025 Act definition is still to be found).
     That is not stated in the circular. It is UNVERIFIED and goes to Q-026.

## Decisions

### A. How Kosh knows which account a trade belongs to

| Option | For | Against |
|---|---|---|
| **1. You choose the account on every import.** The default is the broker's name, so one account per broker needs no thought. You can type another name, for example "Zerodha – joint". | Works for every broker and file. Correct for people with two accounts at one broker. | One more field on the import screen. |
| 2. Read the client code from the file | No typing | Only Angel One files carry it (no samples for others), so it would mean guessing a format. |

**Recommendation: 1.** Kosh remembers the accounts you've used and offers them in a list.

### B. Trades already saved (tasks 1–7)

- **Broker imports:** put into an account named after their broker ("Zerodha", "Upstox",
  "Angel One", or the name you gave a mapped file).
- **CAS (mutual funds):** fund units are already matched per folio, so they keep no demat
  account.
- **Opening holdings and purchases entered for a missing sale:**
  - with one account in the ledger: placed in it;
  - with several: placed in a "Not assigned" account. The Holdings screen asks you to move
    them, and the year's figures carry a warning until you do.
- **Your figures change only if you hold the same share in two or more accounts.** Filed
  years then show the "changed since you filed" notice from task 4.

### C. Transfers between your own accounts

A new **Transfers** section (on the Holdings screen) records each move:

- date, share (ISIN), quantity, from account, to account
- **Leaving the old account:** the shares leave by FIFO, oldest entry first.
- **Arriving in the new account:** they keep their original purchase date and cost (holding
  period, grandfathering), but they queue behind what that account already held on the
  transfer date (Circular 768).
- **Moving more than the old account holds** is refused. The usual cause is that the older
  purchases haven't been imported yet.
- **Transfers are saved per person** and can be removed again.
- **A sale with no holding in its own account** (missing purchase history) gets a hint: "If
  these shares came from another of your accounts, add a transfer".

The "moved from another demat account" choice in the opening-holdings form stays for
accounts whose history you don't have. For those lots Kosh still asks for the original date
and price, plus the date they entered this account.

### D. Same-day trading (intraday) and F&O

- **Intraday:** same-day buys and sells are netted only within one account. A buy at Zerodha
  and a sell at Groww on the same day are two delivery trades, not intraday.
- **F&O:** positions are matched per account, since each broker settles its own.

## Questions for you

1. Approve brief 0003 as recommended? That covers account choice on import (A1), placing
   existing trades by broker (B), and transfers queued by entry date with the original cost
   and date (C).
2. Do you have two accounts **at the same broker**? If not, the account name defaults to the
   broker and you'd never need to type one.

## Open tax questions (logged as Q-026 on approval)

- Does Circular 768 apply under the 2025 Act (s.67(7)(c))?
- Do moved-in shares keep their original holding period and cost, while queueing by their
  entry date into the new account?
- Off-market transfers to family members are real transfers (gifts). They aren't covered here;
  they belong under "gift" in Q-029.

## Answers recorded (2026-10-10)

| # | Answer | Effect |
|---|---|---|
| 1 | **Approved:** keep each account separate for taxation. | Built as recommended. FIFO matching runs per account. The tax itself stays per person: gains from every account are added up, and the ₹1.25 lakh exemption, set-off and carry-forward apply to the total. That's one return per PAN, as today. |
| 2 | Not answered | Default: the account name is the broker's name, and another name can be typed if needed. |

## Implementation notes (2026-10-10)

- **Engine:**
  - `Trade.account` and `Lot.account`, plus `entered_on` for lots that arrived from another
    account.
  - The matcher keeps FIFO queues per (account, instrument), ordered by entry into the
    account (`Lot.entry`).
  - `Transfer` events run after corporate actions and before trades on the same day.
  - Corporate actions apply to each account's holding separately.
  - Intraday netting is grouped by account.
  - Fund units (CAS) keep no account and stay per folio.
- **Notices:**
  - `FIFO_PER_ACCOUNT` (UNVERIFIED, Q-026): added to every share sale's "why?" whenever more
    than one account is involved, or a transfer.
  - `NO_ACCOUNT`: share trades with no account sit next to named ones.
  - `TRANSFER_SHORT`: a transfer moved less than it asked for.
- **Ledger, schema v3:**
  - `trade.entered_on` and a `transfer` table.
  - The upgrade creates one account per broker from saved imports ("Zerodha", "Upstox",
    "Angel One", or the mapped name) and places their trades in it.
  - Opening lots and hand-entered purchases go to the person's only account if there is
    exactly one; otherwise they are left unassigned and the Holdings screen asks.
  - Account names are matched ignoring case and extra spaces.
- **RPC methods:** `ledger_add_transfer` (refused when the source account held fewer shares at
  the start of that day), `ledger_remove_transfer`, `ledger_rename_account` and
  `ledger_assign_account`. `ledger_import` takes `account`, which defaults to the broker's
  name. Ledger replies carry `accounts` and `transfers`.
- **UI:**
  - **Import screen:** a "Demat account" field with the person's accounts suggested.
  - **Opening holdings:** an account column, and an "arrived on" date for lots moved in.
  - **Missing history:** names the account and suggests a transfer.
  - **Holdings screen:** account column, Demat accounts (rename, put unassigned trades into
    one) and Transfers (add, two-step remove).
  - **"Why?":** names the account.

### Fixes from the QA review (2026-10-10)

- **Bonus stripping is per person:** bonus shares held in any of the person's accounts count,
  with the selling account's used first (1961 s.94(8); 2025 s.175(9),(10)).
- **Account names match on letters and digits only,** ignoring case, so "ICICI Direct",
  "icici-direct" and the upgraded "ICICIDIRECT" are one account. This applies to imports,
  transfers, renames and the transfer check.
- **Opening lots:** the account and arrival date are part of their duplicate key, so one
  purchase split across two accounts is two lots. The v3 upgrade and "put in an account"
  recompute these keys.
- **A lot moved in from another account joins the queue on its arrival date,** so it can't
  cover a sale made before it arrived.
- **Hand-entered purchases follow a rename or a "put in an account"** on the page itself.
  They aren't reloaded, which could race with a save still waiting.
- **Unassigned trades can be put into accounts one by one.**
- **The account field resets when the person changes.**
- **"Already imported" names the account the file went into,** and says how to move it.
