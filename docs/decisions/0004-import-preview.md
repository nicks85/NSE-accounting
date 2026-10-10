# 0004 — Preview an import before it is saved

- **Status:** approved 2026-10-10 as recommended (A1, B1, C, D1).
- **Date:** 2026-10-10
- **Builds on:** brief 0001 D6 (task 9), D3 (duplicates, done in task 2), and the Angel One
  charges check (brief 0002)
- **Scope:** RPC (`ledger_import` split into preview and save), Import screen. The importers
  don't change.

## Problem

Today "Import files" reads the files and saves them in one step. You find out what happened
only afterwards: new trades, duplicates skipped, a file refused for conflicts, rows skipped,
charges that don't add up, companies with no ISIN. A wrong file or wrong account then needs
an Undo.

## What already exists (not rebuilt)

- One batch per file, with duplicates skipped, conflicts refused and "already imported"
  recognised before reading (task 2, D3)
- Import history with Undo
- Angel One's charges compared with the file's "Total Trade Charges" summary, as a warning
- Importer notes for skipped rows, guessed segments and encodings

## Decisions

### A. When the preview happens

| Option | For | Against |
|---|---|---|
| **1. Always preview, then "Save"** | You see everything before anything is stored. One flow for every source. | One more click per import. |
| 2. A "preview first" checkbox | Fast path stays | Two flows to test and explain. Undo already covers the fast path. |

**Recommendation: 1.**

### B. How the save step knows what was previewed

| Option | For | Against |
|---|---|---|
| **1. The screen sends the same files again with "save".** The engine re-reads them and refuses if any file's SHA-256 differs from the preview's. | Nothing is held in the engine between the two steps, so a restart or person switch can't leave stale state behind. The engine is the same code path as today. | The file is read twice; under a second for normal tradebooks. |
| 2. The engine keeps the parsed result under a token | Reads once | State held in a long-lived process; it needs expiry, and has to be dropped on a person switch or restore. |

**Recommendation: 1.**

### C. What the preview shows, per file

- **Counts:** trades found, of which new, already saved (skipped), in conflict, and possible
  duplicates from another broker name.
- **Buys and sells,** with the date range.
- **The demat account** the trades will go into.
- **Charges:** total charges excluding STT, and STT.
  - Where the file has its own summary (Angel One today), it is compared with that, and a
    difference of more than ₹1 is a warning.
  - Other files show "no summary in this file to check against". The per-type breakdown
    comes with task 10.
- **Companies imported by name without an ISIN** (Angel One). Mapping them to an ISIN comes
  with task 11. Until then they are listed so you know.
- **Rows skipped,** with the importer's reasons, plus every other importer note.
- **Files that won't be saved:** files already imported (date, and which account), and files
  refused for conflicts (both trades side by side, as today).

### D. What "Save" does when some files can't be saved

| Option | For | Against |
|---|---|---|
| **1. Save the files that are fine; list the ones that aren't.** | Matches how each file is already its own batch, so you can import or undo them separately. | You might expect all or nothing. |
| 2. Save nothing unless every file is fine | All or nothing | One bad yearly file blocks the rest. |

**Recommendation: 1.** The Save button says how many files will be saved, for example
"Save 2 of 3 files".

### E. Changing your mind

- **Cancel** drops the preview; nothing was stored.
- **Changing the source, the account or the files** clears the preview, so it can't be saved
  for the wrong account.
- **Switching person** clears it too.

## Questions for you

1. Approve brief 0004 as recommended? That's A1 (always preview), B1 (resend the files on
   save), C (the contents above) and D1 (save the files that are fine).

## Open tax questions

None. This changes how an import is confirmed, not how anything is taxed. Charges keep the
treatment logged in Q-027.

## Implementation notes (2026-10-10)

- **`ledger_import` modes:**
  - `mode: "preview"` rehearses the save: it saves the files in order inside one transaction,
    then rolls everything back (`Ledger.rehearsal()`). Nothing is stored, and the preview
    can't differ from the save.
  - `mode: "save"` (the default) saves. With `expected` (each file's SHA-256 from the
    preview, in file order) it refuses if the files aren't the ones previewed.
  - **The screen keeps the chosen files.** It reads them again on Save, and the SHA-256
    check guards against a change in between.
- **Per-file preview fields:**
  - `sha256`, `account`;
  - `trades`, `buys`, `sells`, `date_from`, `date_to`;
  - `charges`, `stt`, `stated_charges`, `charges_check` (`matches`, `differs` or `none`, with
    ₹1 of slack for a rounded summary);
  - `by_name` (companies with no ISIN) and `notes` (the importer's notes);
  - the counts and conflicts from task 2.
- **One small importer change:** `ImportResult.stated_charges` (optional) carries a file's
  own "Total Trade Charges". Angel One fills it in; merged files add theirs up when every
  file has one.
- **Screen:**
  - "Import files" is now **Preview import**.
  - The preview panel ("Check before saving") offers **Save this file**, **Save all N
    files**, **Save N of M files** or **Nothing to save**, plus **Cancel**.
  - The preview is dropped when the source, account, files, mapping, password or person
    changes.
- **E2E:** a shared `importFiles()` helper previews, then saves when there is something to
  save.

### Fixes from the QA review (2026-10-10)

- **The preview rehearses the save.**
  - `Ledger.rehearsal()` opens one transaction, saves the files exactly as Save would, in
    order, then rolls everything back. Each step inside runs under a savepoint.
  - So a file now sees the files before it in the same request: overlaps, conflicts between
    two files, and the same file chosen twice preview exactly as they save.
  - `dry_run` is gone.
- **`expected` is a list:** each file's SHA-256 in order, so two files with the same name are
  both checked.
- **Charges check:** compares the file's total with `ImportResult.row_charges`, the importer's
  own sum over every trade row (skipped F&O rows included, as in the file's total). It no
  longer compares with the imported trades only.
- **The screen:**
  - **Inputs lock** while a preview is read, and a reply for inputs that changed meanwhile is
    dropped.
  - **The preview is dropped** after a restore (`revision`) or a change in the saved imports.
  - **It keeps the chosen `File`s,** not their encoded bytes; Save reads them again and the
    engine checks the SHA-256.
  - **After Save,** a notice appears if the saved numbers differ from the preview's.
