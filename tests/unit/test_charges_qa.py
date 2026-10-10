"""QA review of brief 0005 (charges by type): gaps found in review, fixed since. Synthetic only."""

from dataclasses import replace

import pytest

from engine.api import charges_by_type
from engine.models import Segment
from tests.golden.helpers import buy, d, sell


def test_fund_trades_without_charges_are_not_reported_as_missing_charges() -> None:
    fund = "INF000A01011#123"
    trades = [replace(sell("2025-06-02", 10, 12, trade_id=f"CAS:{fund}:2025-06-02:3"),
                      instrument=fund, segment=Segment.MUTUAL_FUND)]
    assert "CAS" not in charges_by_type(trades, 2025).without_charges


def test_hand_entered_and_opening_lots_are_not_reported_as_files() -> None:
    trades = [buy("2025-05-01", 1, 1, trade_id="MANUAL:1"),
              buy("2025-05-02", 1, 1, trade_id="OPENING:INE000A01011:2025-05-02:1")]
    without = charges_by_type(trades, 2025).without_charges
    assert "MANUAL" not in without and "OPENING" not in without


def test_charge_parts_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        replace(buy("2025-05-01", 1, 1, trade_id="A:1"), charges=d(5),
                charge_parts=(("BROKERAGE", d(7)), ("OTHER", d(-2))))
