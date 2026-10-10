"""Duplicate detection for imports (brief 0001 D3).

- **Broker files with trade IDs:** the key is the segment plus the engine's trade id. The
  importers build that id from broker, exchange, trade date and the exchange trade number
  (``ZERODHA:NSE:2025-06-02:123``). Exchange trade numbers are unique only per exchange and
  day, which the id already includes.
- **Files without IDs (the CAS):** the importer's id is the transaction's position in that
  file, which changes when a later CAS covers a longer period. The key is instead a SHA-256 of
  the trade's date, time, instrument, side, quantity and price, plus how many times that same
  combination has appeared so far in the file. Two genuine identical transactions on one day
  stay two trades, and the same transactions in an overlapping CAS match one for one.
"""

import hashlib
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal

from engine.models import Trade

ID_LESS_PREFIXES = ("CAS:", "OPENING:")
"""Trade-id prefixes whose ids are positions in a file or form, not broker trade numbers: the
CAS, and opening holdings (the same lot entered twice is one lot)."""


def _number(value: Decimal) -> str:
    """Same text for 10, 10.0 and 1E+1, so files that print amounts differently still match."""
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


def details(trade: Trade) -> tuple[str, ...]:
    """What must agree for two trades with the same key to be the same trade."""
    return (trade.segment.value, trade.trade_date.isoformat(), trade.instrument,
            trade.side.value, _number(trade.quantity), _number(trade.price))


def dedupe_keys(trades: Sequence[Trade]) -> list[str]:
    """One key per trade, in order (see the module docstring)."""
    seen: Counter[tuple[str, ...]] = Counter()
    keys = []
    for trade in trades:
        if trade.trade_id.startswith(ID_LESS_PREFIXES):
            combination = (*details(trade),
                           trade.executed_at.isoformat() if trade.executed_at else "")
            seen[combination] += 1
            text = "|".join((*combination, str(seen[combination])))
            keys.append("H|" + hashlib.sha256(text.encode()).hexdigest())
        else:
            keys.append(f"{trade.segment.value}|{trade.trade_id}")
    return keys


def same_details(a: Trade, b: Trade) -> bool:
    return details(a) == details(b)
