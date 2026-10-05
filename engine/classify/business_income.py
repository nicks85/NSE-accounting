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


@dataclass(frozen=True, slots=True)
class BusinessIncome:
    speculative: Decimal
    non_speculative: Decimal
    speculative_stt: Decimal
    non_speculative_stt: Decimal


def _net(disposals: Iterable[Disposal]) -> tuple[Decimal, Decimal]:
    items = list(disposals)
    stt = sum((d.stt for d in items), ZERO)
    return sum((d.gain for d in items), ZERO) - stt, stt


def business_income(
    intraday: Iterable[Disposal], fno: Iterable[Disposal]
) -> BusinessIncome:
    speculative, speculative_stt = _net(intraday)
    non_speculative, non_speculative_stt = _net(fno)
    return BusinessIncome(speculative, non_speculative, speculative_stt, non_speculative_stt)
