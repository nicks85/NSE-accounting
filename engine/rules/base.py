"""Rule-pack structure. Each tax year has its own pack module (CLAUDE.md rule 5)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

ACT_2025_URL = (
    "https://www.incometaxindia.gov.in/documents/d/guest/"
    "income_tax_act_2025_as_amended_by_fa_act_2026-pdf"
)
CBDT_CG_FAQ_URL = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2036604"


class Act(StrEnum):
    ACT_1961 = "Income-tax Act, 1961"
    ACT_2025 = "Income-tax Act, 2025"


@dataclass(frozen=True, slots=True)
class Citation:
    """Where a rule comes from. ``question`` points at docs/OPEN_QUESTIONS.md when unverified."""

    topic: str
    act_1961: str | None
    act_2025: str | None
    url: str
    unverified: bool = False
    question: str | None = None

    def section(self, act: Act) -> str:
        """The section to show for a pack's governing Act, falling back to the other Act."""
        primary = self.act_2025 if act is Act.ACT_2025 else self.act_1961
        return primary or self.act_2025 or self.act_1961 or "—"


@dataclass(frozen=True, slots=True)
class RatePeriod:
    """Equity special rates for transfers on or after ``starts_on``."""

    starts_on: date
    stcg_equity: Decimal
    ltcg_equity: Decimal


@dataclass(frozen=True, slots=True)
class RulePack:
    start_year: int
    label: str
    act: Act
    rate_periods: tuple[RatePeriod, ...]
    ltcg_exemption: Decimal
    citations: dict[str, Citation] = field(default_factory=dict)
    listed_long_term_months: int = 12
    grandfathering_before: date = date(2018, 2, 1)
    grandfathering_fmv_date: date = date(2018, 1, 31)
    unlisted_long_term_months: int = 24
    """Fund units (non-equity) and other unlisted assets after 23-Jul-2024."""
    non_equity_cutover: date = date(2024, 7, 23)
    """Non-equity transfers before this used 36 months and 20% with indexation (not
    supported; such lines are flagged for manual computation)."""
    specified_fund_from: date = date(2023, 4, 1)
    other_ltcg_rate: Decimal = Decimal("0.125")
    capital_loss_carry_years: int = 8
    business_loss_carry_years: int = 8
    speculative_loss_carry_years: int = 4

    def rates_on(self, day: date) -> RatePeriod:
        applicable = [p for p in self.rate_periods if p.starts_on <= day]
        if not applicable:
            raise ValueError(f"{self.label}: no rates for {day}")
        return applicable[-1]
