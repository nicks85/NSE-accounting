"""FIFO lot matching per instrument.

Listed shares held in demat form are matched first-in-first-out: Income-tax Act 1961,
s.45(2A) and its Explanation; carried into the Income-tax Act 2025 (see
docs/OPEN_QUESTIONS.md Q-001 for the pending 2025 section citation).
F&O positions are matched FIFO as well (including short positions). That is a matching
convention for business-income computation, not a statutory rule.

Contract: equity trades passed here must be delivery trades only. Same-day intraday buys and
sells are split out by ``engine.classify`` first; otherwise an intraday pair would be matched
against older delivery lots and reported as a capital gain.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from engine.matching.corporate_actions import (
    CorporateAction,
    Split,
    apply_split,
    bonus_lot,
)
from engine.models import Disposal, Lot, Segment, Side, Trade


class InsufficientHoldingsError(ValueError):
    """A cash-equity sell exceeds the quantity held (short delivery is not possible)."""


@dataclass(frozen=True, slots=True)
class MatchResult:
    disposals: tuple[Disposal, ...]
    open_lots: tuple[Lot, ...]
    warnings: tuple[str, ...] = ()


class FifoBook:
    """Mutable per-instrument FIFO queues. Feed trades in chronological order."""

    def __init__(self, opening_lots: Iterable[Lot] = ()) -> None:
        self._lots: dict[str, deque[Lot]] = {}
        self._disposals: list[Disposal] = []
        self._warnings: list[str] = []
        for lot in sorted(opening_lots, key=lambda lot: lot.acquired_on):
            self._lots.setdefault(lot.instrument, deque()).append(lot)

    def lots(self, instrument: str) -> deque[Lot]:
        return self._lots.setdefault(instrument, deque())

    def apply(self, trade: Trade) -> None:
        is_buy = trade.side is Side.BUY
        queue = self.lots(trade.instrument)
        if not is_buy and trade.segment is Segment.EQUITY:
            held = sum((lot.quantity for lot in queue if lot.is_long), Decimal(0))
            if held < trade.quantity:  # checked up front so a failed sell leaves the book intact
                raise InsufficientHoldingsError(
                    f"{trade.trade_id}: selling {trade.quantity} of {trade.instrument} on "
                    f"{trade.trade_date} but only {held} held"
                )
        remaining = trade
        while queue and queue[0].is_long != is_buy:
            lot, rest = queue[0].take(min(remaining.quantity, abs(queue[0].quantity)))
            if rest is None:
                queue.popleft()
            else:
                queue[0] = rest
            closing, remaining_or_none = _split_trade(remaining, abs(lot.quantity))
            self._disposals.append(_disposal(lot, closing))
            if remaining_or_none is None:
                return
            remaining = remaining_or_none

        queue.append(
            Lot(
                instrument=trade.instrument,
                acquired_on=trade.trade_date,
                quantity=remaining.quantity if is_buy else -remaining.quantity,
                value=remaining.value,
                charges=remaining.charges,
                stt=remaining.stt,
                source_trade_id=trade.trade_id,
                segment=trade.segment,
            )
        )

    def apply_action(self, action: CorporateAction) -> None:
        queue = self.lots(action.instrument)
        if isinstance(action, Split):
            lots, warnings = apply_split(list(queue), action)
            queue.clear()
            queue.extend(lots)
        else:
            lot, warnings = bonus_lot(list(queue), action)
            if lot is not None:
                queue.append(lot)
        self._warnings.extend(warnings)

    def result(self) -> MatchResult:
        open_lots = tuple(lot for q in self._lots.values() for lot in q)
        return MatchResult(
            disposals=tuple(self._disposals),
            open_lots=open_lots,
            warnings=tuple(self._warnings),
        )


def match_fifo(
    trades: Iterable[Trade],
    opening_lots: Iterable[Lot] = (),
    actions: Iterable[CorporateAction] = (),
) -> MatchResult:
    """Match trades FIFO, applying corporate actions on their ex-dates.

    Events are ordered by date; on the same date corporate actions come before trades
    (trades on the ex-date are at post-action prices). Input order is kept within a day.
    """
    book = FifoBook(opening_lots)
    events: list[tuple[date, int, Trade | CorporateAction]] = [
        (a.ex_date, 0, a) for a in actions
    ]
    events += [(t.trade_date, 1, t) for t in trades]
    for _, _, event in sorted(events, key=lambda e: (e[0], e[1])):
        if isinstance(event, Trade):
            book.apply(event)
        else:
            book.apply_action(event)
    return book.result()


def _split_trade(trade: Trade, quantity: Decimal) -> tuple[Trade, Trade | None]:
    """Split ``quantity`` units off ``trade``; the remainder keeps exact leftover amounts."""
    if quantity == trade.quantity:
        return trade, None
    head = trade.portion(quantity, "")
    rest = replace(
        trade,
        quantity=trade.quantity - quantity,
        price=trade.price,
        charges=trade.charges - head.charges,
        stt=trade.stt - head.stt,
    )
    return head, rest


def _disposal(lot: Lot, closing: Trade) -> Disposal:
    opening = (lot.value, lot.charges)
    closing_side = (closing.value, closing.charges)
    (buy_value, buy_charges), (sell_value, sell_charges) = (
        (opening, closing_side) if lot.is_long else (closing_side, opening)
    )
    return Disposal(
        instrument=lot.instrument,
        acquired_on=lot.acquired_on,
        sold_on=closing.trade_date,
        quantity=closing.quantity,
        cost=buy_value + buy_charges,
        sale_value=sell_value,
        transfer_expenses=sell_charges,
        stt=lot.stt + closing.stt,
        open_trade_id=lot.source_trade_id,
        close_trade_id=closing.trade_id,
    )
