"""Per-profile settings kept in the ledger (brief 0001 task 3): fund classes, 31-Jan-2018
prices, names, brought-forward losses, hand-entered purchases and excluded sales.

They are saved together as one ``Settings`` value, replacing what was there, in one
transaction: the UI always has the whole picture, so a full replace can't half-apply.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from engine.classify.funds import FundClass
from engine.classify.trades import MANUAL_PREFIX
from engine.models import Side, Trade
from engine.rules.setoff import LossEntry

HOW_ACQUIRED = ("bought", "ipo", "bonus", "gift", "esop", "transfer")
"""How a hand-entered purchase was acquired (Q-029)."""


@dataclass(frozen=True, slots=True)
class ManualBuy:
    """A purchase the user entered for a sale with missing purchase history."""

    trade: Trade
    how: str
    for_trade: str
    """The engine trade id of the sale it was entered for."""

    def __post_init__(self) -> None:
        if not self.trade.trade_id.startswith(MANUAL_PREFIX):
            raise ValueError(f"a hand-entered purchase id starts with {MANUAL_PREFIX!r}")
        if self.trade.side is not Side.BUY:
            raise ValueError(f"{self.trade.trade_id}: a hand-entered purchase must be a buy")
        if self.how not in HOW_ACQUIRED:
            raise ValueError(f"{self.trade.trade_id}: unknown way of acquiring {self.how!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    fund_classes: Mapping[str, FundClass] = field(default_factory=lambda: MappingProxyType({}))
    """ISIN → class."""
    guessed: frozenset[str] = frozenset()
    """ISINs whose class was pre-filled from a CAS guess and not confirmed yet."""
    fmv_2018: Mapping[str, Decimal] = field(default_factory=lambda: MappingProxyType({}))
    """ISIN → 31-Jan-2018 price per share or unit."""
    names: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """ISIN (or ``NAME:`` placeholder) → share or scheme name."""
    brought_forward: tuple[LossEntry, ...] = ()
    manual_buys: tuple[ManualBuy, ...] = ()
    excluded: tuple[str, ...] = ()
    """Engine trade ids of sales left out for missing purchase history (Q-031)."""
    filed_on_time: Mapping[int, bool] = field(default_factory=lambda: MappingProxyType({}))
    """Start year → whether that year's return was filed by the due date (Q-011). A year
    that isn't listed is unknown."""

    @property
    def late_returns(self) -> tuple[int, ...]:
        return tuple(sorted(y for y, on_time in self.filed_on_time.items() if not on_time))

    def __post_init__(self) -> None:
        for isin, price in self.fmv_2018.items():
            if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
                raise ValueError(f"31-Jan-2018 price for {isin} must be a positive Decimal")
        unknown = self.guessed - set(self.fund_classes)
        if unknown:
            raise ValueError(f"guessed classes with no class: {sorted(unknown)}")
        ids = [m.trade.trade_id for m in self.manual_buys]
        if len(ids) != len(set(ids)):
            raise ValueError("hand-entered purchases need distinct ids")
