"""Core value types shared by importers, matching, classification and rules."""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum

from engine.money import ZERO, apportion, require_decimal


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Segment(StrEnum):
    EQUITY = "EQUITY"
    """Cash-market equity (delivery or intraday)."""
    FNO = "FNO"
    """Exchange-traded futures and options."""


@dataclass(frozen=True, slots=True)
class Trade:
    """One executed trade, normalised from any broker.

    ``charges`` are transaction costs other than STT (brokerage, exchange and SEBI fees, stamp
    duty, GST). STT is kept separate because its tax treatment differs by head of income.
    """

    trade_id: str
    trade_date: date
    instrument: str
    """ISIN for equity; exchange contract symbol for F&O."""
    side: Side
    quantity: Decimal
    price: Decimal
    charges: Decimal = ZERO
    stt: Decimal = ZERO
    segment: Segment = Segment.EQUITY

    def __post_init__(self) -> None:
        for name in ("quantity", "price", "charges", "stt"):
            require_decimal(name, getattr(self, name))
        if self.quantity <= 0:
            raise ValueError(f"{self.trade_id}: quantity must be positive")
        if self.price < 0 or self.charges < 0 or self.stt < 0:
            raise ValueError(f"{self.trade_id}: price, charges and stt must be non-negative")

    @property
    def value(self) -> Decimal:
        return self.quantity * self.price

    def portion(self, quantity: Decimal, suffix: str) -> "Trade":
        """A slice of this trade with charges and STT apportioned by quantity."""
        if not 0 < quantity <= self.quantity:
            raise ValueError(f"{self.trade_id}: invalid portion {quantity}")
        return replace(
            self,
            trade_id=f"{self.trade_id}{suffix}",
            quantity=quantity,
            charges=apportion(self.charges, quantity, self.quantity),
            stt=apportion(self.stt, quantity, self.quantity),
        )

    def split(self, quantity: Decimal) -> "tuple[Trade, Trade | None]":
        """Split off the first ``quantity`` units; the remainder keeps the exact leftover
        charges and STT so the two pieces always sum to the original."""
        if quantity == self.quantity:
            return self, None
        head = self.portion(quantity, "")
        rest = replace(
            self,
            quantity=self.quantity - quantity,
            charges=self.charges - head.charges,
            stt=self.stt - head.stt,
        )
        return head, rest


@dataclass(frozen=True, slots=True)
class Lot:
    """An open position, keeping the opening trade's gross value, charges and STT.

    For F&O a negative ``quantity`` is a short position opened by a sell. Cash equity can be
    short only within an intraday book (``intraday=True``), squared off the same day.
    """

    instrument: str
    acquired_on: date
    quantity: Decimal
    value: Decimal
    charges: Decimal
    stt: Decimal
    source_trade_id: str
    segment: Segment = Segment.EQUITY
    intraday: bool = False

    def __post_init__(self) -> None:
        for name in ("quantity", "value", "charges", "stt"):
            require_decimal(name, getattr(self, name))
        if self.quantity == 0:
            raise ValueError("lot quantity must be non-zero")
        if self.quantity < 0 and self.segment is Segment.EQUITY and not self.intraday:
            raise ValueError(f"{self.source_trade_id}: cash-equity lot cannot be short")
        if self.value < 0 or self.charges < 0 or self.stt < 0:
            raise ValueError(f"{self.source_trade_id}: value, charges and stt must be non-negative")

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def cost(self) -> Decimal:
        """Cost of a long lot: gross value plus charges (excluding STT)."""
        if not self.is_long:
            raise ValueError(f"{self.source_trade_id}: a short lot has no cost")
        return self.value + self.charges

    def take(self, quantity: Decimal) -> tuple["Lot", "Lot | None"]:
        """Split off ``quantity`` units; returns (taken, remainder or None)."""
        size = abs(self.quantity)
        if not 0 < quantity <= size:
            raise ValueError(f"{self.source_trade_id}: invalid take {quantity}")
        if quantity == size:
            return self, None
        sign = 1 if self.is_long else -1
        taken = replace(
            self,
            quantity=sign * quantity,
            value=apportion(self.value, quantity, size),
            charges=apportion(self.charges, quantity, size),
            stt=apportion(self.stt, quantity, size),
        )
        rest = replace(
            self,
            quantity=sign * (size - quantity),
            value=self.value - taken.value,
            charges=self.charges - taken.charges,
            stt=self.stt - taken.stt,
        )
        return taken, rest


@dataclass(frozen=True, slots=True)
class Disposal:
    """A matched (opening lot, closing trade) pair produced by the matcher.

    ``cost`` is the buy side's gross value plus its charges; ``sale_value`` is the sell side's
    gross value and ``transfer_expenses`` its charges (all excluding STT). ``stt`` is the STT
    paid on both sides. For a closed F&O short, the sell came first: ``acquired_on`` is then
    the date the position was opened.
    """

    instrument: str
    acquired_on: date
    sold_on: date
    quantity: Decimal
    cost: Decimal
    sale_value: Decimal
    transfer_expenses: Decimal
    stt: Decimal
    open_trade_id: str
    close_trade_id: str

    @property
    def gain(self) -> Decimal:
        """Gain before any tax-specific adjustment (grandfathering, STT deductibility)."""
        return self.sale_value - self.transfer_expenses - self.cost
