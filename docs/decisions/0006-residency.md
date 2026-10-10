# 0006 — Residential status per tax year

- **Status:** approved 2026-10-10 as recommended (A, B1).
- **Date:** 2026-10-10
- **Builds on:** brief 0001 D10 (task 13), Q-028. Question 8 of brief 0001 wasn't answered, so
  this follows that brief's recommendation: warnings only.
- **Scope:** a per-person, per-year setting (the `year_setting.residency` column already
  exists), compute notices, the Gains and Export screens, and the PDF summary

## Problem

Kosh assumes every person is resident in India. Some rules depend on residential status. A
user who was non-resident in a year, or resident but not ordinarily resident (RNOR), isn't
told what differs.

## What the law says (2025 Act text in `docs/sources/`, checked 2026-10-10)

- **Same rates for everyone.** STCG on STT-paid equity is 20% (s.196(1)), and LTCG is 12.5% on
  the gains "exceeding ₹125000" (s.198(2)). Neither section has a residency condition, so a
  non-resident gets the same rates and the same ₹1.25 lakh exemption.
- **The basic-exemption shortfall is for residents only.** "In the case of an individual or a
  Hindu undivided family, being a resident…" (s.196(2), s.197(2), s.198(3)).
  - **What it means:** if other income is below the basic exemption limit, the
    special-rate gains are reduced by the shortfall.
  - **1961 Act counterparts:** s.111A(1) proviso, s.112(1) proviso and s.112A(2). These are
    still to be checked against an official 1961 text (Q-001).
- **The rebate is for residents only.** "An assessee, being an individual resident in India"
  (s.156; 1961 s.87A). It isn't allowed against the s.198 tax either (s.198(7)).

## What changes in Kosh: notices only; no figure changes

Kosh's tax figure applies neither the shortfall nor the rebate today; the SCOPE note says so.
That is already right for a non-resident, and can only overstate a resident's tax, never
understate it. So residency changes the notices and guidance, not the figures:

| Status | Notice in the year's report |
|---|---|
| Resident (default) | Unchanged: the SCOPE note says the shortfall and rebate aren't applied. New: if other income is below the basic exemption limit, actual tax may be lower (s.196(2)/s.198(3)). |
| RNOR | As Resident: an RNOR is a resident, so the shortfall applies (UNVERIFIED, Q-028) |
| Non-resident | The shortfall and rebate don't apply, so the figure needs no such adjustment. Brokers deduct tax at source from a non-resident's gains: match it with Form 26AS (the TDS section is not yet found in the 2025 Act). Treaty (DTAA) relief isn't computed. |

The Export screen and the PDF show the status to select in the return (Part A, which Kosh
doesn't fill, Q-025).

## Decisions

### A. Where it's entered

**Recommendation:** a "Residential status for this year" choice next to the tax year on the
Gains screen (Resident, RNOR, Non-resident). It is saved per person and per year, like
"return filed on time".

### B. An "other income" input to apply the shortfall for residents

| Option | For | Against |
|---|---|---|
| **1. Not now: a notice only** | No new input; Kosh stays a capital-gains tool | A resident with low other income sees a figure higher than their actual tax |
| 2. Ask for "other income" and apply the shortfall | Exact for low-income residents | Kosh would need the slab rates and the old/new regime choice, which is a whole-return computation that is out of scope (Q-012) |

**Recommendation: 1.**

## Questions for you

1. Approve brief 0006 as recommended? That's A, and B1 (notices only).

## Open tax questions (Q-028, updated on approval)

- **Now cited from the 2025 Act:**
  - the rates and the ₹1.25 lakh exemption apply to non-residents too;
  - the shortfall and the rebate are for residents only.
- **Still open:**
  - RNOR treated as resident;
  - the 2025 Act section for TDS on a non-resident's gains;
  - the 1961 Act sections, to check against an official text.

## Implementation notes (2026-10-10)

- **Engine:**
  - `compute_tax_year(..., residency="RES"|"NOR"|"NRI")` and
    `compute_tax_years(..., residency={year: status})`.
  - The report carries `residency`, plus a `RESIDENCY` notice for every status.
  - Citations:
    - `SHORTFALL_RESIDENT_ONLY` is always cited;
    - `REBATE_RESIDENT_ONLY` for non-residents;
    - `RNOR_AS_RESIDENT` (UNVERIFIED, Q-028) for RNOR.
  - The figure never changes.
- **Ledger:** `Settings.residency` is stored in `year_setting.residency` (resident is the
  default and isn't listed). It is saved together with the other settings.
- **RPC:** `compute` takes `residency`, and the settings JSON carries `residency`.
- **PDF:** a "Residential status for this year" line under the title.
- **UI:**
  - A "Residential status for this year" choice next to the tax year on Gains, saved per
    person and year.
  - Export names the status to select in the return.

### Fixes from the QA review (2026-10-10)

- **RNOR as resident is now cited:** 2025 Act s.2(72) and s.6(13). It is no longer marked
  UNVERIFIED.
- **TDS for non-residents** is cited as 2025 Act s.393(2), Table Sl. No. 17 (1961 Act s.195).
- **Notices cite both Acts,** so a 1961-Act year (up to FY 2025-26) shows its own sections too.
- **A status changed after a year was marked filed** now appears in the "changed since filing"
  list as "Residential status", since it is chosen in Part A of the return.
- **`docs/sources/README.md`** lists the new sections checked against the 2025 Act.
- **Export** shows the status from the person's settings straight away.
- **The status choice** is described for screen readers and locked while settings load.
