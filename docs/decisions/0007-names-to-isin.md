# 0007 — Company names to ISINs, offline (task 11)

- **Status:** approved 2026-10-10 with **option a only** (answer: "a"). You confirm each name once and it is remembered. Saved trades are rewritten as in C. Sources c and d are not used. b comes with task 14.
- **Date:** 2026-10-10
- **Replaces:** brief 0001 D7's bundled security master, which the exchanges' terms don't allow
  (below)
- **Related:** brief 0002 (Angel One imported by name), Q-032, Q-024 (ETFs by name)

## Problem

Angel One's trade history gives company names, usually cut to about 20 characters ("EXAMPLE
DEPO SER (I)"), and no ISIN. Kosh imports such trades under a `NAME:` key. Gains are right, but:

- Schedule 112A needs the ISIN for shares bought on or before 31-Jan-2018.
- An ETF imported by name is taxed as a share, which is wrong for gold, debt or international
  ETFs (Q-024).
- Mapping a name to an ISIN later must rewrite the trades already saved. A re-import with the
  ISIN would otherwise conflict, as found in task 2.

## What the exchanges allow (checked 2026-10-10)

- **NSE's terms of use:** "any information or content or data on the Website / Mobile
  Application shall not be copied, modified, reverse engineer, reproduced, uploaded,
  transmitted, posted, stored …, distributed in any form, without prior written permission of
  NSE".
- **BSE's disclaimer:** no part "may be reproduced, stored in a retrieval system or
  transmitted in any form … without the prior written permission".

So Kosh **can't bundle** a list built from the exchanges' files. The plan in brief 0001 D7 is
dropped.

## Other sources

| Source | Licence | Coverage | Notes |
|---|---|---|---|
| **a. You confirm each name once; Kosh remembers it** | Your own data | Every name | Always correct. One answer per new name, per broker. |
| **b. Your Angel One Tax P&L** | Your own data | Every share you have sold | The delivery section has ISIN and Scrip Name per sale (layout seen in brief 0001). A trade-history sell is matched to the Tax P&L row with the same date, quantity and price. It needs a Tax P&L reader, which is task 14 for Angel One; the layout is known from your files. |
| **c. A list you download yourself** (for example NSE's "securities available for trading") | Your own download, read on your computer, never shipped with Kosh | Current listings | Kosh only reads a file you give it, as with a tradebook. Whether NSE's terms allow you to keep a copy for personal use is for you to judge. |
| **d. `casparser-isin`'s ISIN table** (already shipped inside Kosh, for CAS imports) | The package is MIT; **the data's source and licence aren't stated** | 107,155 active equity ISINs with issuer names, including delisted and renamed ones | It looks like the depository's ISIN master. Using it for suggestions adds nothing new to what Kosh already ships, but the data licence is unknown (Q-032). |

## Decisions

### A. How a name gets its ISIN

**Recommendation:**

- Always a (you confirm). Suggestions come from b, c and d where available:
  1. exact matches from your Tax P&L first;
  2. then fuzzy matches by name, up to three, from c or d.
- **Nothing is mapped without your confirmation,** except an exact Tax P&L match on date,
  quantity and price.
- **Your answer is remembered per broker and per name as written** (the `symbol_alias`
  table), so you are asked once.

### B. Using d (the table already shipped)

| Option | For | Against |
|---|---|---|
| **1. Use it for suggestions only, and log the data licence as open in Q-032** | Works offline from day one, and is already in the app | Data licence unknown |
| 2. Don't use it until the maintainer confirms the source and licence | No new licence question | No suggestions unless you give your own list (c) or a Tax P&L (b) |

**Recommendation: 2.** Correctness and licensing over convenience; a and b work without it. I
can ask the maintainer: it's a public GitHub project. Separately, this raises whether Kosh
should go on shipping that table at all. It is used for the CAS today. That is logged in Q-032.

### C. Rewriting trades already saved

**Recommendation:** when you confirm a name, every saved trade under `NAME:<name>` for that
person changes to the ISIN, in one transaction. Their duplicate keys, settings (names, FMV)
and transfers move with them. The import history and filed-year snapshots are untouched; a
filed year then shows "changed since filing" only if a figure changes.

### D. What's in this task

- Confirming names, remembered answers and the rewrite: a and C.
- Reading a list you give it: c.
- Matching against an Angel One Tax P&L (b) is task 14's reader and comes with it.

## Questions for you

1. Approve brief 0007 as recommended? That's A, B2 (don't use the shipped table yet), C and D.
2. Shall I draft a short question for the `casparser-isin` maintainers (a GitHub issue) about
   where its ISIN data comes from and its licence? You would post it yourself: it is public and
   from your account.

## Open questions

Q-032 is rewritten: the exchanges' terms, the unknown data licence of the shipped table, and
whether Kosh should keep shipping it.

## Implementation notes (2026-10-10)

- **The ISIN check digit** (`engine/identifiers.py`) moved from the importers to the engine, so
  the ledger can check an ISIN you type.
- **Ledger:**
  - `map_name(person, broker, name, isin)` remembers the answer in `symbol_alias` and moves,
    in one transaction, the person's trades, transfers and settings from `NAME:<name>` to the
    ISIN. The 112A name, FMV and fund class move too, keeping anything already set for the
    ISIN.
  - Every trade read by name keeps the name as written in `trade.raw_symbol`, including
    trades imported after the match (the Angel One importer passes each trade's name), so
    `unmap_name` finds them all.
  - Duplicate keys use the broker's trade id and don't change, so a later file with the ISIN
    is a duplicate, not a conflict.
  - Two names for one company end up together.
- **RPC:**
  - `ledger_map_name` and `ledger_unmap_name`, whose replies carry the settings.
  - Ledger replies carry `names_unmapped` and `names_mapped`.
  - Angel One imports use the remembered names (`isin_map`), so a later file arrives with
    ISINs.
- **Screen:** "Companies imported by name" on the Import screen: type an ISIN (it must look
  like one; the engine checks the digit), Save, and Undo.
- **Not here:**
  - suggestions from a list you download (option c);
  - the shipped table (option d, Q-032);
  - matching against an Angel One Tax P&L (option b), which comes with task 14.

### Fixes from the QA review (2026-10-10, two rounds)

- **Names are confirmed per person,** not shared by everyone in the ledger. They are kept in a new
  `name_alias` table (schema version 4), listed on screen from that table, and used only for
  that person's imports. The `symbol_alias` table from brief 0001 is no longer used for this.
- **Undo keeps a company together.** Every trade read under the name goes back, including those
  imported after the match and purchases entered by hand under the name (a settings save no
  longer drops their name). The upgrade to version 4 records the name on trades already saved
  by name.
- **Undo puts back what the match moved.** The match records the name's and the ISIN's settings
  and the name's transfers.
  - **Transfers** go back if the match moved them, or if the shares they move can only be the
    name's: the sending account holds nothing else of the ISIN, directly or through earlier
    transfers.
  - **Settings** (fund class, 31-Jan-2018 price, 112A name): the name gets back what it had,
    and what the match put on the ISIN is taken off again. A setting changed on the ISIN since
    the match stays on the ISIN if the person also holds the ISIN another way. Otherwise it
    was about the name's shares, so it goes with them.
  - **Two names on one ISIN:** undoing one leaves the other's settings on the ISIN, in either
    order.
- **Two judgment calls, accepted in review:**
  - **A price changed on a shared ISIN after the match stays on the ISIN** after Undo. The name
    gets back the price it had before; if it had none, its pre-2018 lots have no price until it
    is entered again.
  - **A transfer from an account holding two matched names of the ISIN stays on the ISIN** when
    one of them is undone, since it may carry the other name's shares.
- **A purchase entered for a sale read by name** keeps that name, even if it was entered after
  the match, so Undo keeps the sale and its purchase together.
- **Different values are reported.** If the name and the ISIN had different 31-Jan-2018 prices or
  fund classes, the ISIN's is kept and the screen says so, since two values for one company
  usually mean one is a mistake.
- **A guessed fund class stays marked as guessed** when it moves to the ISIN.
- **Smaller fixes:**
  - The name is compared as the importer writes it (upper case, single spaces).
  - A name already matched to another ISIN must be undone first.
  - Undoing a name that was never matched is refused, and leaves nothing behind.
  - Hand-entered purchases aren't counted as imported trades.
- **Screen:**
  - A match waits for any settings save still running. Saves are held while it runs, and a
    save queued meanwhile is dropped in favour of the reply's settings. A setting changed
    elsewhere in that second or so is lost (the screen then shows the reply's values).
  - Each Save button names its company for screen readers.
  - A pasted ISIN may contain spaces, and a hint says why Save is disabled.
  - The text says names are remembered per person, and what Undo does.
