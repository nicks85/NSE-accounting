"""Capital gains on delivery equity: holding period, grandfathering, rate bucket.

Citations live in the rule pack (``engine/rules/common.py``):
- short-term if held for not more than 12 months — 2025 Act s.2(101)(b); 1961 Act s.2(42A)
- gain = sale value - transfer expenses - cost, STT not deductible — 2025 Act s.72; 1961 s.48
- grandfathering for shares acquired before 1-Feb-2018 — 2025 Act s.90(7); 1961 s.55(2)(ac)
- rates — 2025 Act s.196 / s.198; 1961 Act s.111A / s.112A
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from engine.dates import add_months
from engine.models import Disposal
from engine.rules import common
from engine.rules.base import Citation, RulePack


class Term(StrEnum):
    SHORT = "STCG"
    LONG = "LTCG"


@dataclass(frozen=True, slots=True, order=True)
class Bucket:
    """Gains taxed together: a term and a special rate."""

    term: Term
    rate: Decimal

    @property
    def label(self) -> str:
        percent = format((self.rate * 100).normalize(), "f")
        return f"{self.term.value} @ {percent}%"


@dataclass(frozen=True, slots=True)
class CapitalGainLine:
    disposal: Disposal
    bucket: Bucket
    long_term_after: date
    """The sale is long-term if made after this date."""
    cost: Decimal
    """Cost used in the computation (after grandfathering)."""
    grandfathered_fmv: Decimal | None
    """FMV x quantity on 31-Jan-2018 when grandfathering applied, else None."""
    gain: Decimal
    citations: tuple[Citation, ...]


def capital_gain_line(
    disposal: Disposal, pack: RulePack, fmv_2018: Mapping[str, Decimal]
) -> tuple[CapitalGainLine, list[str]]:
    """Compute one disposal's capital gain under ``pack``. Returns (line, warnings)."""
    warnings: list[str] = []
    long_term_after = add_months(disposal.acquired_on, pack.listed_long_term_months)
    term = Term.LONG if disposal.sold_on > long_term_after else Term.SHORT
    rates = pack.rates_on(disposal.sold_on)
    rate = rates.ltcg_equity if term is Term.LONG else rates.stcg_equity
    citations = [common.HOLDING_PERIOD, common.FIFO, common.COMPUTATION,
                 common.LTCG_EQUITY if term is Term.LONG else common.STCG_EQUITY,
                 common.STT_PAID_ASSUMED]

    cost = disposal.cost
    fmv_value: Decimal | None = None
    if term is Term.LONG and disposal.acquired_on < pack.grandfathering_before:
        per_share = fmv_2018.get(disposal.instrument)
        if per_share is None:
            warnings.append(
                f"{disposal.close_trade_id}: {disposal.instrument} acquired "
                f"{disposal.acquired_on} needs its 31-Jan-2018 FMV for grandfathering; "
                "actual cost used (may overstate tax)"
            )
        else:
            fmv_value = per_share * disposal.quantity / disposal.split_factor
            cost = max(disposal.cost, min(fmv_value, disposal.sale_value))
            citations.append(common.GRANDFATHERING)

    gain = disposal.sale_value - disposal.transfer_expenses - cost
    line = CapitalGainLine(
        disposal=disposal,
        bucket=Bucket(term, rate),
        long_term_after=long_term_after,
        cost=cost,
        grandfathered_fmv=fmv_value,
        gain=gain,
        citations=tuple(citations),
    )
    return line, warnings
