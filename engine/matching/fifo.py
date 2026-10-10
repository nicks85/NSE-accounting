"""FIFO lot matching per instrument.

Cost of acquisition and period of holding of securities held in demat form are determined
first-in-first-out: Income-tax Act 2025 s.67(7)(c); Income-tax Act 1961 s.45(2A).
Source: docs/sources/income-tax-act-2025-as-amended-by-fa-2026.pdf (official text from
https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf).
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

from engine.classify.funds import is_fund
from engine.dates import add_months
from engine.matching.corporate_actions import (
    BONUS_PREFIX,
    Bonus,
    CorporateAction,
    Split,
    apply_split,
    bonus_lot,
    bonus_lot_id,
)
from engine.models import Disposal, Lot, Segment, Side, Trade


class InsufficientHoldingsError(ValueError):
    """A cash-equity or fund sell exceeds the quantity held (short delivery is not possible)."""


@dataclass(frozen=True, slots=True)
class Shortfall:
    """The part of a sell with no earlier purchase to match: "missing purchase history".

    The matched part of the sell (if any) is disposed of normally; this part is left out, so no
    cost is ever assumed for it. Which lots a sale used is only provisional while a shortfall
    is unresolved: the missing purchase is likely older than every known lot, and FIFO would
    have used it first.
    """

    trade_id: str
    instrument: str
    sold_on: date
    quantity: Decimal
    price: Decimal
    segment: Segment

    @property
    def sale_value(self) -> Decimal:
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class MatchResult:
    disposals: tuple[Disposal, ...]
    open_lots: tuple[Lot, ...]
    warnings: tuple[str, ...] = ()
    shortfalls: tuple[Shortfall, ...] = ()


class FifoBook:
    """Mutable per-instrument FIFO queues. Feed trades in chronological order."""

    def __init__(self, opening_lots: Iterable[Lot] = (), *, allow_short: bool = False,
                 collect_shortfalls: bool = False) -> None:
        """``allow_short`` lets cash-equity sells open short positions; used only for
        intraday trades, which are squared off the same day. ``collect_shortfalls`` records a
        sell beyond the quantity held as a ``Shortfall`` instead of raising."""
        self._allow_short = allow_short
        self._collect = collect_shortfalls
        self._shortfalls: list[Shortfall] = []
        self._lots: dict[str, deque[Lot]] = {}
        self._disposals: list[Disposal] = []
        self._warnings: list[str] = []
        self._bonuses: dict[str, list[Bonus]] = {}
        for lot in sorted(opening_lots, key=lambda lot: lot.acquired_on):
            self._lots.setdefault(lot.instrument, deque()).append(lot)
            if lot.source_trade_id.startswith(BONUS_PREFIX):
                self._warnings.append(
                    f"{lot.source_trade_id}: bonus shares brought in as an opening lot; bonus "
                    "stripping can't be checked for sales of the original shares without the "
                    "bonus action (docs/OPEN_QUESTIONS.md Q-014)"
                )

    def lots(self, instrument: str) -> deque[Lot]:
        return self._lots.setdefault(instrument, deque())

    def apply(self, trade: Trade) -> None:
        first_new = len(self._disposals)
        self._apply(trade)
        if trade.side is Side.SELL and not self._allow_short:
            self._strip_bonus(trade.instrument, first_new)

    def _strip_bonus(self, instrument: str, first_new: int) -> None:
        """Bonus stripping — Income-tax Act 2025 s.175(9),(10); 1961 Act s.94(8).

        A loss on securities bought within 3 months before a bonus record date and sold within
        9 months after it is ignored, if bonus securities are still held after the sale; the
        ignored loss becomes the cost of the bonus securities still held. Window boundaries are
        a best guess (docs/OPEN_QUESTIONS.md Q-014).
        """
        queue = self.lots(instrument)
        for index in range(first_new, len(self._disposals)):
            disposal = self._disposals[index]
            if disposal.gain >= 0:
                continue
            for action in self._bonuses.get(instrument, []):
                record = action.record_on
                # (a) bought within 3 months before the record date, and (b) held when the
                # entitlement was fixed: a buy on or after the ex-date gets no bonus.
                bought_in_window = add_months(record, -3) <= disposal.acquired_on < record
                entitled = disposal.acquired_on < action.ex_date
                sold_in_window = record < disposal.sold_on <= add_months(record, 9)
                lot_id = bonus_lot_id(action)
                # (c) bonus shares already allotted and still held after the sale.
                held = [i for i, lot in enumerate(queue)
                        if lot.source_trade_id == lot_id and lot.acquired_on <= disposal.sold_on]
                if not (bought_in_window and entitled and sold_in_window and held):
                    continue
                loss = -disposal.gain
                self._disposals[index] = replace(disposal, stripped_loss=loss)
                bonus = queue[held[0]]
                queue[held[0]] = replace(bonus, value=bonus.value + loss)
                break

    def _apply(self, trade: Trade) -> None:
        is_buy = trade.side is Side.BUY
        queue = self.lots(trade.instrument)
        if not is_buy and trade.segment is not Segment.FNO and not self._allow_short:
            held = sum((lot.quantity for lot in queue if lot.is_long), Decimal(0))
            if held < trade.quantity:  # checked up front so a failed sell leaves the book intact
                if not self._collect:
                    raise InsufficientHoldingsError(
                        f"{trade.trade_id}: selling {trade.quantity} of {trade.instrument} on "
                        f"{trade.trade_date} but only {held} held"
                    )
                self._shortfalls.append(Shortfall(
                    trade.trade_id, trade.instrument, trade.trade_date,
                    trade.quantity - held, trade.price, trade.segment))
                if held <= 0:
                    return
                trade, _ = trade.split(held)
        remaining = trade
        while queue and queue[0].is_long != is_buy:
            lot, rest = queue[0].take(min(remaining.quantity, abs(queue[0].quantity)))
            if rest is None:
                queue.popleft()
            else:
                queue[0] = rest
            closing, remaining_or_none = remaining.split(abs(lot.quantity))
            self._disposals.append(_disposal(lot, closing))
            if remaining_or_none is None:
                return
            remaining = remaining_or_none

        _enqueue(
            queue,
            Lot(
                instrument=trade.instrument,
                acquired_on=trade.trade_date,
                quantity=remaining.quantity if is_buy else -remaining.quantity,
                value=remaining.value,
                charges=remaining.charges,
                stt=remaining.stt,
                source_trade_id=trade.trade_id,
                segment=trade.segment,
                intraday=self._allow_short,
            )
        )

    def apply_action(self, action: CorporateAction) -> None:
        if is_fund(action.instrument):
            self._warnings.append(
                f"{action.instrument}: {type(action).__name__.lower()} on {action.ex_date} "
                "ignored: corporate actions on fund units (splits, bonus, scheme mergers) "
                "aren't supported yet (docs/OPEN_QUESTIONS.md Q-023)"
            )
            return
        queue = self.lots(action.instrument)
        if isinstance(action, Split):
            lots, warnings = apply_split(list(queue), action)
            queue.clear()
            queue.extend(lots)
        else:
            lot, warnings = bonus_lot(list(queue), action)
            if lot is not None:
                _enqueue(queue, lot)
                self._bonuses.setdefault(action.instrument, []).append(action)
        self._warnings.extend(warnings)

    def result(self) -> MatchResult:
        open_lots = tuple(lot for q in self._lots.values() for lot in q)
        warnings = list(self._warnings)
        warnings += [
            f"{lot.source_trade_id}: intraday position of {lot.quantity} {lot.instrument} "
            f"opened on {lot.acquired_on} was not squared off"
            for lot in open_lots
            if lot.intraday
        ]
        return MatchResult(
            disposals=tuple(self._disposals),
            open_lots=open_lots,
            warnings=tuple(warnings),
            shortfalls=tuple(self._shortfalls),
        )


def match_fifo(
    trades: Iterable[Trade],
    opening_lots: Iterable[Lot] = (),
    actions: Iterable[CorporateAction] = (),
    *,
    allow_short: bool = False,
    collect_shortfalls: bool = False,
) -> MatchResult:
    """Match trades FIFO, applying corporate actions on their ex-dates.

    Events are ordered by date; on the same date corporate actions come before trades
    (trades on the ex-date are at post-action prices). Input order is kept within a day.
    """
    book = FifoBook(opening_lots, allow_short=allow_short,
                    collect_shortfalls=collect_shortfalls)
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
        split_factor=lot.split_factor,
        segment=lot.segment,
    )


def _enqueue(queue: deque[Lot], lot: Lot) -> None:
    """Insert ``lot`` keeping the queue ordered by acquisition date (stable for equal dates).

    Needed because a bonus lot is dated at allotment, which can be after buys made on or just
    after the ex-date; FIFO follows the order shares were acquired.
    """
    index = len(queue)
    while index > 0 and queue[index - 1].acquired_on > lot.acquired_on:
        index -= 1
    queue.insert(index, lot)
