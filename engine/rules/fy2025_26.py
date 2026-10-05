"""FY 2025-26 (AY 2026-27) — Income-tax Act, 1961.

Equity rates as set by Finance (No. 2) Act 2024 for transfers from 23-Jul-2024: s.111A STCG
20%, s.112A LTCG 12.5% above ₹1.25 lakh. Source: CBDT FAQs (PIB release 2036604),
https://www.pib.gov.in/PressReleasePage.aspx?PRID=2036604 (copy in docs/sources/).
"""

from datetime import date
from decimal import Decimal

from engine.rules import common
from engine.rules.base import Act, RatePeriod, RulePack
from engine.rules.common import COMMON

PACK = RulePack(
    start_year=2025,
    label="FY 2025-26",
    act=Act.ACT_1961,
    rate_periods=(
        RatePeriod(date(2025, 4, 1), stcg_equity=Decimal("0.20"), ltcg_equity=Decimal("0.125")),
    ),
    ltcg_exemption=Decimal(125000),
    citations={**COMMON, common.BONUS_STRIPPING_1961.topic: common.BONUS_STRIPPING_1961},
)
