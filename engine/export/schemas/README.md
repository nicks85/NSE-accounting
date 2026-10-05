# Official CBDT ITR JSON schemas

Downloaded at development time only; the app never fetches anything at runtime. Official files
use CRLF line endings; the committed copies use LF, and their JSON content was checked to be
identical to the official downloads on 5-Oct-2026.

| File | Form | Assessment year | Version | Source | SHA-256 of committed file |
|---|---|---|---|---|---|
| `ITR-2_AY2026-27.json` | ITR-2 | 2026-27 (FY 2025-26) | V1.2 | https://www.incometax.gov.in/iec/foportal/sites/default/files/2026-08/ITR-2_2026_Main_V1.2.json | `b35f9052616a1b00d8d9810ebe08b4efbbb2d02e45bb78f77146cbcf0ce9a1cf` |
| `ITR-3_AY2026-27.json` | ITR-3 | 2026-27 (FY 2025-26) | V1.1 | https://www.incometax.gov.in/iec/foportal/sites/default/files/2026-07/ITR-3_2026_Main_V1.1.json | `a1bd1a4020db0faff2ad1af738fd2ea599790e7c3b265fc4f5d979e280f0b8ae` |

ITR-3 V1.1 differs from the June 2026 file (`2026-06/ITR-3_2026_Main.json`) only by the new
TDS code 194T (payments to partners).

Which form: ITR-2 for capital gains without business income; ITR-3 when there is intraday
(speculative) or F&O (non-speculative business) income.
