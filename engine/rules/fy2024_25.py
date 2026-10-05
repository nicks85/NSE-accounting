"""FY 2024-25 (AY 2025-26) — Income-tax Act, 1961.

Finance (No. 2) Act 2024 changed equity rates for transfers on or after 23-Jul-2024:
s.111A STCG 15% → 20%; s.112A LTCG 10% → 12.5%. The s.112A exemption rose from ₹1 lakh to
₹1.25 lakh for the whole of FY 2024-25. Source: CBDT FAQs (PIB release 2036604, Q2, Q6, Q7),
https://www.pib.gov.in/PressReleasePage.aspx?PRID=2036604 (copy in docs/sources/).

UNVERIFIED (Q-007): the ₹1.25 lakh exemption is applied to gains taxed at 12.5% before gains
taxed at 10%.
"""

from datetime import date
from decimal import Decimal

from engine.rules import common
from engine.rules.base import CBDT_CG_FAQ_URL, Act, Citation, RatePeriod, RulePack
from engine.rules.common import COMMON

EXEMPTION_ORDER = Citation(
    "₹1.25 lakh exemption applied to 12.5% gains before 10% gains", "s.112A", None,
    CBDT_CG_FAQ_URL, unverified=True, question="Q-007")

PACK = RulePack(
    start_year=2024,
    label="FY 2024-25",
    act=Act.ACT_1961,
    rate_periods=(
        RatePeriod(date(2024, 4, 1), stcg_equity=Decimal("0.15"), ltcg_equity=Decimal("0.10")),
        RatePeriod(date(2024, 7, 23), stcg_equity=Decimal("0.20"), ltcg_equity=Decimal("0.125")),
    ),
    ltcg_exemption=Decimal(125000),
    citations={**COMMON, EXEMPTION_ORDER.topic: EXEMPTION_ORDER,
               common.BONUS_STRIPPING_1961.topic: common.BONUS_STRIPPING_1961},
)
