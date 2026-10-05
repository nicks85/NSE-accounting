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

from engine.classify.funds import FOLIO_SEPARATOR, FundClass, isin_of
from engine.dates import add_months
from engine.models import Disposal, Segment
from engine.money import INTERNAL_SCALE
from engine.notices import Notice
from engine.rules import common
from engine.rules.base import Citation, RulePack


class Term(StrEnum):
    SHORT = "STCG"
    LONG = "LTCG"


class Regime(StrEnum):
    EQUITY = "equity"
    """STT-paid listed shares and equity-oriented funds: s.196/s.198 (1961: s.111A/s.112A)."""
    OTHER = "other"
    """Other assets: s.197 (1961: s.112) for LTCG, slab rates for STCG."""


@dataclass(frozen=True, slots=True)
class Bucket:
    """Gains taxed together. ``rate`` None means the normal slab rates (outside Kosh)."""

    term: Term
    rate: Decimal | None
    regime: Regime = Regime.EQUITY

    @property
    def label(self) -> str:
        if self.rate is None:
            return f"{self.term.value} (slab rate)"
        percent = format((self.rate * 100).normalize(), "f")
        suffix = "" if self.regime is Regime.EQUITY else " (other assets)"
        return f"{self.term.value} @ {percent}%{suffix}"

    @property
    def exemption_eligible(self) -> bool:
        """Only LTCG under s.198 / s.112A gets the ₹1.25 lakh exemption."""
        return self.term is Term.LONG and self.regime is Regime.EQUITY

    @property
    def sort_key(self) -> tuple[int, Decimal, str, str]:
        """Highest rate first; slab-rate buckets count as highest (up to 30%+), Q-008."""
        return (0 if self.rate is None else 1, -(self.rate or Decimal(0)), self.term.value,
                self.regime.value)


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
    manual: bool = False
    """True when Kosh can't compute this line (e.g. pre-23-Jul-2024 indexed LTCG on non-equity
    funds); it is excluded from the totals and flagged."""


BOUNDARY_DAYS = 3
"""Sales this close to the 12-month boundary are flagged (day-count convention, Q-013)."""


def capital_gain_line(
    disposal: Disposal,
    pack: RulePack,
    fmv_2018: Mapping[str, Decimal],
    fund_classes: Mapping[str, FundClass] | None = None,
) -> tuple[CapitalGainLine, list[Notice]]:
    """Compute one disposal's capital gain under ``pack``. Returns (line, notices)."""
    if disposal.segment is Segment.MUTUAL_FUND:
        fund_class = (fund_classes or {}).get(isin_of(disposal.instrument))
        if fund_class is not FundClass.EQUITY_ORIENTED:
            return _non_equity_fund_line(disposal, pack, fund_class)
    warnings: list[Notice] = []
    long_term_after = add_months(disposal.acquired_on, pack.listed_long_term_months)
    term = Term.LONG if disposal.sold_on > long_term_after else Term.SHORT
    rates = pack.rates_on(disposal.sold_on)
    rate = rates.ltcg_equity if term is Term.LONG else rates.stcg_equity
    citations = [common.HOLDING_PERIOD, common.FIFO, common.COMPUTATION,
                 common.LTCG_EQUITY if term is Term.LONG else common.STCG_EQUITY,
                 common.STT_PAID_ASSUMED]
    if disposal.segment is Segment.MUTUAL_FUND:
        citations += [*_fund_citations(disposal), common.EQUITY_FUND]
    if abs((disposal.sold_on - long_term_after).days) <= BOUNDARY_DAYS:
        citations.append(common.HOLDING_BOUNDARY)

    cost = disposal.cost
    fmv_value: Decimal | None = None
    if term is Term.LONG and disposal.acquired_on < pack.grandfathering_before:
        per_share = fmv_2018.get(isin_of(disposal.instrument))
        if per_share is None:
            warnings.append(Notice(
                "MISSING_FMV",
                f"{disposal.instrument} acquired {disposal.acquired_on} needs its 31-Jan-2018 "
                "FMV (NAV for fund units) for grandfathering; actual cost used (may overstate "
                "tax)",
                ref=disposal.close_trade_id,
            ))
        else:
            fmv_value = (per_share * disposal.quantity / disposal.split_factor).quantize(
                INTERNAL_SCALE)
            cost = max(disposal.cost, min(fmv_value, disposal.sale_value))
            citations.append(common.GRANDFATHERING)
            if disposal.split_factor != 1:
                citations.append(common.GRANDFATHERING_AFTER_SPLIT)

    gain = disposal.sale_value - disposal.transfer_expenses - cost + disposal.stripped_loss
    if disposal.stripped_loss:
        citations.append(pack.citations[common.BONUS_STRIPPING.topic])
        citations.append(common.BONUS_STRIPPING_WINDOW)
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


def _fund_citations(disposal: Disposal) -> list[Citation]:
    citations = [common.FUND_CLASS]
    if FOLIO_SEPARATOR in disposal.instrument:
        citations.append(common.FUND_FIFO_PER_FOLIO)
    return citations


def _non_equity_fund_line(
    disposal: Disposal, pack: RulePack, fund_class: FundClass | None
) -> tuple[CapitalGainLine, list[Notice]]:
    """Specified (debt) and other funds — 2025 Act s.76, s.2(101)(a), s.197; 1961 Act s.50AA,
    s.2(42A), s.112. A missing class is treated as OTHER and flagged."""
    warnings: list[Notice] = []
    citations = [*_fund_citations(disposal), common.COMPUTATION]
    if fund_class is None:
        warnings.append(Notice(
            "FUND_CLASS_MISSING",
            f"{isin_of(disposal.instrument)} has no fund class; treated as 'other' (not "
            "equity-oriented, not specified). Classify it to get the right treatment.",
            question="Q-019", ref=disposal.close_trade_id,
        ))
    gain = disposal.sale_value - disposal.transfer_expenses - disposal.cost
    long_term_after = add_months(disposal.acquired_on, pack.unlisted_long_term_months)
    manual = False
    if fund_class is FundClass.SPECIFIED and disposal.acquired_on >= pack.specified_fund_from:
        bucket = Bucket(Term.SHORT, None, Regime.OTHER)
        long_term_after = date.max
        citations.append(common.SPECIFIED_FUND)
    elif disposal.sold_on < pack.non_equity_cutover:
        manual = True  # 36 months and 20% with indexation applied before 23-Jul-2024
        bucket = Bucket(Term.SHORT, None, Regime.OTHER)
        warnings.append(Notice(
            "MANUAL",
            f"{disposal.instrument} redeemed {disposal.sold_on}, before 23-Jul-2024: non-equity "
            "fund gains then used a 36-month holding period and 20% with indexation, which "
            "Kosh doesn't compute. Excluded from the totals; compute this line manually.",
            question="Q-021", ref=disposal.close_trade_id,
        ))
    elif disposal.sold_on > long_term_after:
        bucket = Bucket(Term.LONG, pack.other_ltcg_rate, Regime.OTHER)
        citations += [common.UNLISTED_HOLDING, common.OTHER_LTCG]
    else:
        bucket = Bucket(Term.SHORT, None, Regime.OTHER)
        citations += [common.UNLISTED_HOLDING, common.SLAB_STCG]
    line = CapitalGainLine(
        disposal=disposal,
        bucket=bucket,
        long_term_after=long_term_after,
        cost=disposal.cost,
        grandfathered_fmv=None,
        gain=gain,
        citations=tuple(citations),
        manual=manual,
    )
    return line, warnings
