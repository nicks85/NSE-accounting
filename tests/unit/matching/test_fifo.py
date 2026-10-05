from datetime import date

import pytest

from engine.matching.fifo import InsufficientHoldingsError, match_fifo
from engine.models import Lot, Segment
from tests.factories import dec, trade


def test_sell_matches_oldest_lot_first() -> None:
    result = match_fifo([
        trade("BUY", "2023-01-10", 10, 100, trade_id="B1"),
        trade("BUY", "2023-06-10", 10, 150, trade_id="B2"),
        trade("SELL", "2024-08-01", 10, 200, trade_id="S1"),
    ])
    [d] = result.disposals
    assert (d.open_trade_id, d.acquired_on, d.cost, d.sale_value) == (
        "B1", date(2023, 1, 10), dec(1000), dec(2000))
    assert d.gain == dec(1000)
    [lot] = result.open_lots
    assert (lot.source_trade_id, lot.quantity) == ("B2", dec(10))


def test_sell_spanning_lots_splits_sale_value_and_charges() -> None:
    result = match_fifo([
        trade("BUY", "2023-01-10", 4, 100, charges=4, trade_id="B1"),
        trade("BUY", "2023-02-10", 6, 110, charges=6, trade_id="B2"),
        trade("SELL", "2024-08-01", 7, 120, charges=7, stt=7, trade_id="S1"),
    ])
    first, second = result.disposals
    assert (first.quantity, first.cost, first.sale_value, first.transfer_expenses) == (
        dec(4), dec(404), dec(480), dec(4))
    assert (second.quantity, second.cost, second.sale_value, second.transfer_expenses) == (
        dec(3), dec(333), dec(360), dec(3))
    assert first.stt + second.stt == dec(7)
    [lot] = result.open_lots
    assert (lot.quantity, lot.cost) == (dec(3), dec(333))


def test_uneven_apportionment_sums_exactly() -> None:
    result = match_fifo([
        trade("BUY", "2023-01-01", 3, 10, charges=1),
        trade("SELL", "2023-02-01", 1, 11, charges=1),
        trade("SELL", "2023-03-01", 1, 11, charges=1),
        trade("SELL", "2023-04-01", 1, 11, charges=1),
    ])
    assert sum(d.cost for d in result.disposals) == dec(31)
    assert not result.open_lots


def test_trades_are_sorted_by_date_stably() -> None:
    result = match_fifo([
        trade("SELL", "2024-02-01", 5, 20, trade_id="S1"),
        trade("BUY", "2024-01-01", 5, 10, trade_id="B1"),
    ])
    assert result.disposals[0].open_trade_id == "B1"


def test_overselling_cash_equity_raises() -> None:
    with pytest.raises(InsufficientHoldingsError, match="only 5 held"):
        match_fifo([trade("BUY", "2024-01-01", 5, 10), trade("SELL", "2024-01-02", 6, 10)])


def test_opening_lots_are_matched_before_new_buys() -> None:
    opening = Lot("INE000A01011", date(2015, 5, 5), dec(2), dec(50), dec(0), dec(0), "OPEN")
    result = match_fifo(
        [trade("BUY", "2024-01-01", 2, 90), trade("SELL", "2024-06-01", 2, 100)],
        opening_lots=[opening],
    )
    assert result.disposals[0].acquired_on == date(2015, 5, 5)


def test_fno_short_then_cover() -> None:
    fut = "NIFTY24AUGFUT"
    result = match_fifo([
        trade("SELL", "2024-08-01", 25, 24000, charges=20, stt=60,
              instrument=fut, segment=Segment.FNO, trade_id="S1"),
        trade("BUY", "2024-08-05", 25, 23800, charges=20,
              instrument=fut, segment=Segment.FNO, trade_id="B1"),
    ])
    [d] = result.disposals
    assert d.open_trade_id == "S1" and d.close_trade_id == "B1"
    assert d.acquired_on == date(2024, 8, 1)
    assert d.cost == dec(25 * 23800 + 20)
    assert d.sale_value == dec(25 * 24000)
    assert d.transfer_expenses == dec(20)
    assert d.gain == dec(5000 - 40)
    assert d.stt == dec(60)
    assert not result.open_lots


def test_fno_sell_beyond_long_opens_short() -> None:
    opt = "NIFTY24AUG24000CE"
    result = match_fifo([
        trade("BUY", "2024-08-01", 50, 100, instrument=opt, segment=Segment.FNO),
        trade("SELL", "2024-08-02", 75, 120, instrument=opt, segment=Segment.FNO),
    ])
    [d] = result.disposals
    assert d.quantity == dec(50) and d.gain == dec(1000)
    [short] = result.open_lots
    assert short.quantity == dec(-25) and short.value == dec(3000)
