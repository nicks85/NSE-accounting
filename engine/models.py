"""Core value types shared by importers, matching, classification and rules."""

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from engine.money import ZERO, apportion, require_decimal

CHARGE_KINDS = ("BROKERAGE", "GST", "EXCHANGE", "SEBI", "STAMP", "IPFT", "OTHER")
"""Charge types other than STT, as the ledger stores them (brief 0005)."""


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Segment(StrEnum):
    EQUITY = "EQUITY"
    """Cash-market equity (delivery or intraday)."""
    FNO = "FNO"
    """Exchange-traded futures and options."""
    MUTUAL_FUND = "MF"
    """Mutual fund units bought from / redeemed with the fund (e.g. from a CAS)."""


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
    executed_at: datetime | None = None
    """Execution timestamp when the source has one; used to order trades within a day."""
    account: str | None = None
    """The demat account (or trading account for F&O) the trade was made in. FIFO runs per
    account (CBDT Circular 768, brief 0003). None is one unnamed account: with no accounts
    named, matching is the same as before accounts existed."""
    entered_on: date | None = None
    """For a purchase that came in from another of the user's accounts (an opening holding
    "moved from another demat account"): the date it entered this account. FIFO queues by
    that date while the holding period runs from ``trade_date`` (Q-026)."""
    charge_parts: tuple[tuple[str, Decimal], ...] = field(default=(), compare=False)
    """``charges`` by type when the file gives them (brief 0005): pairs of a kind from
    ``CHARGE_KINDS`` and an amount, adding up to ``charges``. Empty when not broken down.
    Information only: matching and tax use ``charges``. A slice of a trade (``portion``,
    ``split``) carries none, since only the whole trade's figures are on a contract note."""

    def __post_init__(self) -> None:
        for name in ("quantity", "price", "charges", "stt"):
            require_decimal(name, getattr(self, name))
        if self.quantity <= 0:
            raise ValueError(f"{self.trade_id}: quantity must be positive")
        if self.price < 0 or self.charges < 0 or self.stt < 0:
            raise ValueError(f"{self.trade_id}: price, charges and stt must be non-negative")
        if self.charge_parts:
            kinds = [kind for kind, _ in self.charge_parts]
            if any(k not in CHARGE_KINDS for k in kinds) or len(set(kinds)) != len(kinds):
                raise ValueError(f"{self.trade_id}: charge kinds must be distinct, from "
                                 f"{', '.join(CHARGE_KINDS)}")
            for _, amount in self.charge_parts:
                require_decimal("charge part", amount)
                if amount <= 0:
                    raise ValueError(f"{self.trade_id}: each charge by type must be positive")
            if sum((a for _, a in self.charge_parts), ZERO) != self.charges:
                raise ValueError(f"{self.trade_id}: charges by type don't add up to charges")

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
            charge_parts=(),
        )

    def split(
        self, quantity: Decimal, head_suffix: str = "", rest_suffix: str = ""
    ) -> "tuple[Trade, Trade | None]":
        """Split off the first ``quantity`` units; the remainder keeps the exact leftover
        charges and STT so the two pieces always sum to the original. Suffixes are appended
        to the pieces' trade IDs when they must stay distinguishable."""
        if quantity == self.quantity:
            return replace(self, trade_id=f"{self.trade_id}{head_suffix}", charge_parts=()), None
        head = self.portion(quantity, head_suffix)
        rest = replace(
            self,
            trade_id=f"{self.trade_id}{rest_suffix}",
            quantity=self.quantity - quantity,
            charges=self.charges - head.charges,
            stt=self.stt - head.stt,
            charge_parts=(),
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
    split_factor: Decimal = Decimal(1)
    """Shares per share held on 31-Jan-2018, from splits/consolidations after that date. The
    published 31-Jan-2018 FMV is divided by this for grandfathering (Q-006)."""
    account: str | None = None
    entered_on: date | None = None
    """When the lot entered its current account, if not on ``acquired_on`` (moved in from
    another of the user's accounts). FIFO order follows this (Circular 768)."""

    @property
    def entry(self) -> date:
        """The date FIFO orders by: entry into the account."""
        return self.entered_on or self.acquired_on

    def __post_init__(self) -> None:
        for name in ("quantity", "value", "charges", "stt"):
            require_decimal(name, getattr(self, name))
        if self.quantity == 0:
            raise ValueError("lot quantity must be non-zero")
        if self.quantity < 0 and self.segment is not Segment.FNO and not self.intraday:
            raise ValueError(f"{self.source_trade_id}: cash-equity or fund lot cannot be short")
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
    split_factor: Decimal = Decimal(1)
    segment: Segment = Segment.EQUITY
    stripped_loss: Decimal = Decimal(0)
    """Loss ignored under the bonus-stripping rule (moved into the bonus shares' cost)."""
    account: str | None = None

    @property
    def gain(self) -> Decimal:
        """Gain before grandfathering; a bonus-stripped loss is added back (so it is ignored)."""
        return self.sale_value - self.transfer_expenses - self.cost + self.stripped_loss


@dataclass(frozen=True, slots=True)
class Transfer:
    """Shares moved between two of the user's own demat accounts (brief 0003 C).

    Not a transfer for capital gains, since the owner doesn't change (reading of 1961 Act
    s.2(47); the 2025 Act definition is still to be checked, Q-026): the lots keep their purchase
    date and cost. They leave ``from_account`` FIFO and queue in
    ``to_account`` by the date they arrive (CBDT Circular 768: "the basis for determining the
    movement out of the account is the date of entry into the account").
    """

    transfer_id: str
    on: date
    instrument: str
    quantity: Decimal
    from_account: str | None
    to_account: str | None

    def __post_init__(self) -> None:
        require_decimal("quantity", self.quantity)
        if self.quantity <= 0:
            raise ValueError(f"{self.transfer_id}: quantity must be positive")
        if self.from_account == self.to_account:
            raise ValueError(f"{self.transfer_id}: the two accounts must differ")
