"""Tax Year 2026-27 — Income-tax Act, 2025 (in force from 1-Apr-2026).

s.196(1): STCG on STT-paid equity shares at 20%. s.198(2)(a): LTCG on STT-paid equity
exceeding ₹1,25,000 at 12.5%. Budget 2026 (Finance Act 2026) left these unchanged.
Source: docs/sources/income-tax-act-2025-as-amended-by-fa-2026.pdf, official text from
https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf
"""

from datetime import date
from decimal import Decimal

from engine.rules.base import Act, RatePeriod, RulePack
from engine.rules.common import COMMON

PACK = RulePack(
    start_year=2026,
    label="TY 2026-27",
    act=Act.ACT_2025,
    rate_periods=(
        RatePeriod(date(2026, 4, 1), stcg_equity=Decimal("0.20"), ltcg_equity=Decimal("0.125")),
    ),
    ltcg_exemption=Decimal(125000),
    citations=dict(COMMON),
)
