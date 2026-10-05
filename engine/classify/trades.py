"""Split trades into delivery, intraday (speculative) and F&O (non-speculative).

Intraday: a cash-equity purchase and sale of the same scrip on the same day is settled
without delivery, so it is a speculative transaction — Income-tax Act 1961 s.43(5): "a
transaction in which a contract for the purchase or sale of any commodity, including stocks
and shares, is periodically or ultimately settled otherwise than by the actual delivery or
transfer of the commodity or scrips". Source: https://www.incometaxindia.gov.in/w/section-43-59

F&O: an eligible transaction in derivatives on a recognised stock exchange is not speculative —
s.43(5) proviso, clause (d). Same source.

Income-tax Act 2025 equivalents: pending, see docs/OPEN_QUESTIONS.md Q-001.

UNVERIFIED (Q-004): which units count as intraday when a scrip has several trades on one day,
or is also held from earlier, is a netting convention (same-day buys and sells netted, first
units first), not something the Act spells out.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from engine.models import Segment, Side, Trade
from engine.money import ZERO

UNVERIFIED_INTRADAY_NETTING = True  # surfaced as a warning whenever intraday is found
INTRADAY_SUFFIX = "#intraday"
DELIVERY_SUFFIX = "#delivery"


@dataclass(frozen=True, slots=True)
class ClassifiedTrades:
    delivery: tuple[Trade, ...]
    """Cash-equity trades (or parts) settled by delivery: capital gains."""
    intraday: tuple[Trade, ...]
    """Cash-equity trades (or parts) squared off the same day: speculative business."""
    fno: tuple[Trade, ...]
    """Exchange-traded derivatives: non-speculative business."""
    warnings: tuple[str, ...] = ()


def classify_trades(trades: Iterable[Trade]) -> ClassifiedTrades:
    """Classify trades. Input order is kept within each (instrument, day) group."""
    delivery: list[Trade] = []
    intraday: list[Trade] = []
    fno: list[Trade] = []
    groups: dict[tuple[str, date], list[Trade]] = {}
    for trade in trades:
        if trade.segment is Segment.FNO:
            fno.append(trade)
        else:
            groups.setdefault((trade.instrument, trade.trade_date), []).append(trade)

    for group in groups.values():
        bought = sum((t.quantity for t in group if t.side is Side.BUY), ZERO)
        sold = sum((t.quantity for t in group if t.side is Side.SELL), ZERO)
        squared = min(bought, sold)
        left = {Side.BUY: squared, Side.SELL: squared}
        for trade in group:
            take = min(left[trade.side], trade.quantity)
            left[trade.side] -= take
            if take == 0:
                delivery.append(trade)
                continue
            if take == trade.quantity:
                intraday.append(trade)
                continue
            head, rest = trade.split(take, INTRADAY_SUFFIX, DELIVERY_SUFFIX)
            intraday.append(head)
            if rest is not None:  # pragma: no branch - take < quantity here
                delivery.append(rest)

    warnings: tuple[str, ...] = ()
    if intraday:
        days = len({(t.instrument, t.trade_date) for t in intraday})
        warnings = (
            f"Intraday trades on {days} scrip-day(s) were classified using an UNVERIFIED "
            "same-day netting convention, see docs/OPEN_QUESTIONS.md Q-004",
        )
    return ClassifiedTrades(tuple(delivery), tuple(intraday), tuple(fno), warnings)
