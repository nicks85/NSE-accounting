"""Split trades into delivery, intraday (speculative) and F&O (non-speculative).

Intraday: a cash-equity purchase and sale of the same scrip on the same day is settled
without delivery, so it is a speculative transaction — "a transaction in which a contract for
the purchase or sale of any commodity, including stocks and shares, is periodically or
ultimately settled otherwise than by the actual delivery or transfer of the commodity or
scrips": Income-tax Act 2025 s.66(31); 1961 Act s.43(5). Speculation business is distinct
from any other business: 2025 Act s.26(3).

F&O: a specified derivative transaction on a recognised stock exchange is excluded from
speculative transactions: 2025 Act s.66(31)(a), s.66(33); 1961 Act s.43(5) proviso (d).

Sources: docs/sources/income-tax-act-2025-as-amended-by-fa-2026.pdf (official text from
https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf);
1961 Act: https://www.incometaxindia.gov.in/w/section-43-59

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
OPENING_PREFIX = "OPENING:"
"""Trade-id prefix for opening holdings: lots held before the first imported tradebook, entered
by the user (brief 0001 D5). Like hand-entered purchases they are always delivery."""
MANUAL_PREFIX = "MANUAL:"
"""Trade-id prefix for purchases the user entered by hand (missing purchase history). Such a
buy stands for shares already held before any imported trade, so it is always delivery and is
never netted against a same-day sale as intraday."""


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
    # Same-day netting happens within one account: a buy at one broker and a sale at another
    # on the same day are two delivery trades (brief 0003 D).
    groups: dict[tuple[str | None, str, date], list[Trade]] = {}
    for trade in trades:
        if trade.segment is Segment.FNO:
            fno.append(trade)
        elif trade.segment is Segment.MUTUAL_FUND:
            delivery.append(trade)  # bought from / redeemed with the fund: never intraday
        elif trade.trade_id.startswith((MANUAL_PREFIX, OPENING_PREFIX)):
            delivery.append(trade)
        else:
            groups.setdefault((trade.account, trade.instrument, trade.trade_date),
                              []).append(trade)

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
        days = len({(t.account, t.instrument, t.trade_date) for t in intraday})
        warnings = (
            f"Intraday trades on {days} scrip-day(s) were classified using an UNVERIFIED "
            "same-day netting convention, see docs/OPEN_QUESTIONS.md Q-004",
        )
    return ClassifiedTrades(tuple(delivery), tuple(intraday), tuple(fno), warnings)
