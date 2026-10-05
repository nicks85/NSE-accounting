# Open questions

Tax rules that are ambiguous, unverified, or have conflicting sources. Every rule shipped with
`UNVERIFIED = True` must have an entry here. Resolve with a citation, then remove the flag.

## Q-001 — Income-tax Act 2025 section numbers not verified against the official text

- **Area:** citations for every rule and for FIFO matching.
- **Problem:** the official text of the Income-tax Act 2025 (as amended by Finance Act 2026) at
  https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf
  returns HTTP 403 to automated fetches from the dev environment, so section numbers could not
  be checked against the primary source.
- **Provisional mapping (secondary source: cleartax.in old-vs-new mapping table):**
  45→67, 48→72, 70→108, 71→109, 72→112, 73→113, 74→111, 111A→196, 112A→198.
  Not yet mapped: 2(42A), 43(5), 45(2A) Explanation (FIFO), 55(2)(ac), 55(2)(aa).
- **Needed:** a human-downloaded copy of the official PDF (or the CBDT concordance table) to
  confirm each number.
- **Status:** open.
