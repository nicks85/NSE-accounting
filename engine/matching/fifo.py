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
from engine.models import Disposal, Lot, Segment, Side, Trade, Transfer


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
    account: str | None = None

    @property
    def sale_value(self) -> Decimal:
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class MatchResult:
    disposals: tuple[Disposal, ...]
    open_lots: tuple[Lot, ...]
    warnings: tuple[str, ...] = ()
    shortfalls: tuple[Shortfall, ...] = ()
    transfer_gaps: tuple[tuple[Transfer, Decimal], ...] = ()
    """Transfers that moved less than asked, with the quantity that wasn't held."""


Key = tuple[str | None, str]
"""(account, instrument): FIFO runs per demat account (CBDT Circular 768, brief 0003)."""


class FifoBook:
    """Mutable FIFO queues per (account, instrument). Feed events in chronological order."""

    def __init__(self, opening_lots: Iterable[Lot] = (), *, allow_short: bool = False,
                 collect_shortfalls: bool = False) -> None:
        """``allow_short`` lets cash-equity sells open short positions; used only for
        intraday trades, which are squared off the same day. ``collect_shortfalls`` records a
        sell beyond the quantity held as a ``Shortfall`` instead of raising."""
        self._allow_short = allow_short
        self._collect = collect_shortfalls
        self._shortfalls: list[Shortfall] = []
        self._lots: dict[Key, deque[Lot]] = {}
        self._disposals: list[Disposal] = []
        self._warnings: list[str] = []
        self._bonuses: dict[str, list[Bonus]] = {}
        self._transfer_gaps: list[tuple[Transfer, Decimal]] = []
        for lot in sorted(opening_lots, key=lambda lot: lot.entry):
            self._lots.setdefault((lot.account, lot.instrument), deque()).append(lot)
            if lot.source_trade_id.startswith(BONUS_PREFIX):
                self._warnings.append(
                    f"{lot.source_trade_id}: bonus shares brought in as an opening lot; bonus "
                    "stripping can't be checked for sales of the original shares without the "
                    "bonus action (docs/OPEN_QUESTIONS.md Q-014)"
                )

    def lots(self, instrument: str, account: str | None = None) -> deque[Lot]:
        return self._lots.setdefault((account, instrument), deque())

    def apply(self, trade: Trade) -> None:
        first_new = len(self._disposals)
        self._apply(trade)
        if trade.side is Side.SELL and not self._allow_short:
            self._strip_bonus(trade.instrument, first_new, trade.account)

    def apply_transfer(self, transfer: Transfer) -> None:
        """Move shares between two of the user's accounts: they leave the source FIFO and
        queue in the destination by the transfer date, keeping purchase date and cost."""
        source = self.lots(transfer.instrument, transfer.from_account)
        held = sum((lot.quantity for lot in source if lot.is_long), Decimal(0))
        moving = min(held, transfer.quantity)
        if moving < transfer.quantity:
            self._transfer_gaps.append((transfer, transfer.quantity - moving))
        target = self.lots(transfer.instrument, transfer.to_account)
        while moving > 0:
            lot, rest = source[0].take(min(moving, source[0].quantity))
            if rest is None:
                source.popleft()
            else:
                source[0] = rest
            moving -= lot.quantity
            _enqueue(target, replace(lot, account=transfer.to_account, entered_on=transfer.on))

    def _strip_bonus(self, instrument: str, first_new: int, account: str | None) -> None:
        """Bonus stripping — Income-tax Act 2025 s.175(9),(10); 1961 Act s.94(8).

        A loss on securities bought within 3 months before a bonus record date and sold within
        9 months after it is ignored, if bonus securities are still held after the sale; the
        ignored loss becomes the cost of the bonus securities still held. Window boundaries are
        a best guess (docs/OPEN_QUESTIONS.md Q-014).

        The rule is per person, not per demat account: bonus shares still held in any of the
        person's accounts count (the selling account's are used first).
        """
        queues = [self.lots(instrument, account)] + [
            q for (acct, inst), q in self._lots.items() if inst == instrument and acct != account]
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
                held = [(q, i) for q in queues for i, lot in enumerate(q)
                        if lot.source_trade_id == lot_id and lot.acquired_on <= disposal.sold_on]
                if not (bought_in_window and entitled and sold_in_window and held):
                    continue
                loss = -disposal.gain
                self._disposals[index] = replace(disposal, stripped_loss=loss)
                queue, at = held[0]
                queue[at] = replace(queue[at], value=queue[at].value + loss)
                break

    def _apply(self, trade: Trade) -> None:
        is_buy = trade.side is Side.BUY
        queue = self.lots(trade.instrument, trade.account)
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
                    trade.quantity - held, trade.price, trade.segment, trade.account))
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
                account=trade.account,
                entered_on=trade.entered_on,
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
        # The action applies to the holding in every account, each on its own.
        keys = [k for k in self._lots if k[1] == action.instrument] or [(None, action.instrument)]
        warnings: list[str] = []
        for key in keys:
            queue = self.lots(action.instrument, key[0])
            if isinstance(action, Split):
                lots, notes = apply_split(list(queue), action)
                queue.clear()
                queue.extend(lots)
            else:
                lot, notes = bonus_lot(list(queue), action)
                if lot is not None:
                    _enqueue(queue, replace(lot, account=key[0]))
            warnings += [n for n in notes if n not in warnings]
        if isinstance(action, Bonus):
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
        warnings += [
            f"{t.transfer_id}: moving {t.quantity} {t.instrument} from "
            f"{t.from_account or 'the unnamed account'} on {t.on}, but {gap} weren't held there; "
            "only what was held was moved"
            for t, gap in self._transfer_gaps
        ]
        return MatchResult(
            disposals=tuple(self._disposals),
            open_lots=open_lots,
            warnings=tuple(warnings),
            shortfalls=tuple(self._shortfalls),
            transfer_gaps=tuple(self._transfer_gaps),
        )


def match_fifo(
    trades: Iterable[Trade],
    opening_lots: Iterable[Lot] = (),
    actions: Iterable[CorporateAction] = (),
    *,
    allow_short: bool = False,
    collect_shortfalls: bool = False,
    transfers: Iterable[Transfer] = (),
) -> MatchResult:
    """Match trades FIFO per (account, instrument), applying corporate actions on their
    ex-dates and transfers between the user's accounts on their dates.

    Events are ordered by date. On one date: corporate actions first (trades on the ex-date
    are at post-action prices), then transfers (shares must be in an account to be sold
    from it, and a purchase settles a day later so can't be moved the same day), then trades
    in input order.
    """
    book = FifoBook(opening_lots, allow_short=allow_short,
                    collect_shortfalls=collect_shortfalls)
    events: list[tuple[date, int, Trade | CorporateAction | Transfer]] = [
        (a.ex_date, 0, a) for a in actions
    ]
    events += [(t.on, 1, t) for t in transfers]
    # A purchase that arrived from another of the user's accounts joins this one on arrival.
    events += [(t.entered_on or t.trade_date, 2, t) for t in trades]
    for _, _, event in sorted(events, key=lambda e: (e[0], e[1])):
        if isinstance(event, Trade):
            book.apply(event)
        elif isinstance(event, Transfer):
            book.apply_transfer(event)
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
        account=lot.account,
    )


def _enqueue(queue: deque[Lot], lot: Lot) -> None:
    """Insert ``lot`` keeping the queue ordered by entry into the account (stable for equal
    dates). Entry is the acquisition date, except for shares moved in from another of the
    user's accounts, which enter on the transfer date (Circular 768).

    Needed because a bonus lot is dated at allotment, which can be after buys made on or just
    after the ex-date; FIFO follows the order shares came into the account.
    """
    index = len(queue)
    while index > 0 and queue[index - 1].entry > lot.entry:
        index -= 1
    queue.insert(index, lot)
