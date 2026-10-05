"""Helpers for building synthetic trades in tests."""

from datetime import date
from decimal import Decimal

from engine.models import Segment, Side, Trade

_counter = 0


def dec(value: str | int) -> Decimal:
    return Decimal(value)


def trade(
    side: str,
    on: str,
    qty: str | int,
    price: str | int,
    *,
    instrument: str = "INE000A01011",
    charges: str | int = 0,
    stt: str | int = 0,
    segment: Segment = Segment.EQUITY,
    trade_id: str | None = None,
) -> Trade:
    global _counter
    _counter += 1
    return Trade(
        trade_id=trade_id or f"T{_counter}",
        trade_date=date.fromisoformat(on),
        instrument=instrument,
        side=Side(side),
        quantity=dec(qty),
        price=dec(price),
        charges=dec(charges),
        stt=dec(stt),
        segment=segment,
    )
