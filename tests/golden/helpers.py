"""Shared helpers for golden cases. All data is synthetic."""

from datetime import date
from decimal import Decimal
from typing import Any

from engine.api import TaxYearReport
from engine.classify.capital_gains import Bucket, Term
from engine.models import Segment, Trade
from tests.factories import trade

A = "INE000A01011"
B = "INE000B01012"
FUT = "NIFTY25JUNFUT"


def buy(on: str, qty: int, price: str | int, instrument: str = A, **kw: Any) -> Trade:
    return trade("BUY", on, qty, price, instrument=instrument, **kw)


def sell(on: str, qty: int, price: str | int, instrument: str = A, **kw: Any) -> Trade:
    return trade("SELL", on, qty, price, instrument=instrument, **kw)


def fut(side: str, on: str, qty: int, price: int, **kw: Any) -> Trade:
    return trade(side, on, qty, price, instrument=FUT, segment=Segment.FNO, **kw)


def d(value: str | int) -> Decimal:
    return Decimal(value)


def taxable(report: TaxYearReport) -> dict[str, Decimal]:
    """Non-zero taxable gains per bucket label."""
    return {b.label: v for b, v in sorted(report.setoff.gains.items()) if v}


def st(rate: str) -> Bucket:
    return Bucket(Term.SHORT, Decimal(rate))


def lt(rate: str) -> Bucket:
    return Bucket(Term.LONG, Decimal(rate))


def day(iso: str) -> date:
    return date.fromisoformat(iso)
