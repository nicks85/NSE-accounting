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
- **Also unverified:** the incometaxindia.gov.in section pages cited in docstrings (e.g.
  `/w/section-55-59` for s.55(2)(aa)(iiia)) were found by search but return 403, so the
  page content and version could not be confirmed.

## Q-002 — Treatment of share splits (sub-division)

- **Area:** `engine/matching/corporate_actions.py` (`Split`, flagged `UNVERIFIED_SPLIT`).
- **Implemented as:** total cost of each lot unchanged, apportioned over the new quantity;
  acquisition date (holding period) carried over from the original shares.
- **Problem:** this is standard practice, but no statutory provision or CBDT circular has been
  confirmed for it yet.
- **Needed:** a citation (section, circular or binding ruling) or CA confirmation.
- **Status:** open.

## Q-003 — Bonus share acquisition date: allotment date vs ex-date

- **Area:** `engine/matching/corporate_actions.py` (`Bonus.allotment_date`).
- **Implemented as:** bonus shares' holding period starts on the allotment date when it is
  supplied, else on the ex-date. Allotment is usually a day or two after the record date.
- **Problem:** using the ex-date can shift a sale across the 12-month STCG/LTCG boundary by a
  day or two. Broker/CAS files may or may not include the allotment date.
- **Needed:** confirm allotment date is the correct start (general rule of s.2(42A)) and find
  where importers can get it.
- **Status:** open.
