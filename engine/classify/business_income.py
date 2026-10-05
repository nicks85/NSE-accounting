"""Speculative (intraday) and non-speculative (F&O) business income from matched disposals.

Income = sum of gains (charges already deducted) minus STT, which is deductible for business
income: 2025 Act s.32(k); 1961 Act s.36(1)(xv). Classification: 2025 Act s.66(31), s.66(33);
1961 Act s.43(5). Expenses other than trade charges (internet, advisory, etc.) are not modelled.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from engine.models import Disposal
from engine.money import ZERO
from engine.rules import common
from engine.rules.base import Citation


@dataclass(frozen=True, slots=True)
class BusinessLine:
    """One matched intraday or F&O round trip, for the "why?" drill-down."""

    disposal: Disposal
    speculative: bool
    income: Decimal
    """Gain after charges, minus STT."""
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class BusinessIncome:
    speculative: Decimal
    non_speculative: Decimal
    speculative_stt: Decimal
    non_speculative_stt: Decimal
    lines: tuple[BusinessLine, ...] = ()


def _net(disposals: Iterable[Disposal]) -> tuple[Decimal, Decimal]:
    items = list(disposals)
    stt = sum((d.stt for d in items), ZERO)
    return sum((d.gain for d in items), ZERO) - stt, stt


def business_income(
    intraday: Iterable[Disposal], fno: Iterable[Disposal]
) -> BusinessIncome:
    intraday, fno = list(intraday), list(fno)
    speculative, speculative_stt = _net(intraday)
    non_speculative, non_speculative_stt = _net(fno)
    lines = [
        BusinessLine(d, kind is common.SPECULATIVE, d.gain - d.stt,
                     (kind, common.STT_BUSINESS_DEDUCTION))
        for kind, items in ((common.SPECULATIVE, intraday), (common.NON_SPECULATIVE, fno))
        for d in items
    ]
    return BusinessIncome(speculative, non_speculative, speculative_stt, non_speculative_stt,
                          tuple(lines))
