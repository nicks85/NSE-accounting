import random
from datetime import date

import pytest

from engine.matching.fifo import FifoBook, InsufficientHoldingsError, match_fifo
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


FNO = Segment.FNO


def test_fno_buy_beyond_short_flips_long() -> None:
    result = match_fifo([
        trade("SELL", "2024-08-01", 3, 100, charges=1, stt=1, instrument="O", segment=FNO,
              trade_id="S"),
        trade("BUY", "2024-08-02", 7, 90, charges=7, stt=7, instrument="O", segment=FNO,
              trade_id="B"),
    ])
    [d] = result.disposals
    assert (d.quantity, d.sale_value, d.transfer_expenses, d.cost, d.stt) == (
        dec(3), dec(300), dec(1), dec(273), dec(4))
    [lot] = result.open_lots
    assert (lot.quantity, lot.value, lot.charges, lot.stt) == (dec(4), dec(360), dec(4), dec(4))


def test_fno_partial_cover_of_short() -> None:
    result = match_fifo([
        trade("SELL", "2024-08-01", 10, 50, charges=10, instrument="O", segment=FNO),
        trade("BUY", "2024-08-02", 4, 40, instrument="O", segment=FNO),
    ])
    [d] = result.disposals
    assert (d.sale_value, d.transfer_expenses, d.cost) == (dec(200), dec(4), dec(160))
    [short] = result.open_lots
    assert (short.quantity, short.value, short.charges) == (dec(-6), dec(300), dec(6))


def test_zero_price_option_expiry_long_and_short() -> None:
    long_ = match_fifo([
        trade("BUY", "2024-08-01", 50, "12.5", charges=20, instrument="L", segment=FNO),
        trade("SELL", "2024-08-29", 50, 0, instrument="L", segment=FNO),
    ])
    assert long_.disposals[0].gain == dec(-645)
    short = match_fifo([
        trade("SELL", "2024-08-01", 50, "12.5", charges=20, instrument="S", segment=FNO),
        trade("BUY", "2024-08-29", 50, 0, instrument="S", segment=FNO),
    ])
    assert short.disposals[0].gain == dec(605)


def test_multiple_instruments_are_independent() -> None:
    result = match_fifo([
        trade("BUY", "2024-01-01", 5, 10, instrument="A"),
        trade("BUY", "2024-01-02", 5, 20, instrument="B"),
        trade("SELL", "2024-02-01", 5, 30, instrument="B"),
    ])
    [d] = result.disposals
    assert (d.instrument, d.cost) == ("B", dec(100))
    assert [lot.instrument for lot in result.open_lots] == ["A"]


def test_amounts_are_conserved_across_random_fno_trades() -> None:
    rng = random.Random(7)
    for _ in range(200):
        trades = [
            trade(
                rng.choice(["BUY", "SELL"]), f"2024-08-{1 + i % 28:02d}", rng.randint(1, 9),
                f"{rng.randint(0, 9999)}.{rng.randint(0, 99):02d}",
                charges=f"{rng.randint(0, 99)}.{rng.randint(0, 99):02d}",
                stt=f"{rng.randint(0, 9)}.{rng.randint(0, 99):02d}",
                instrument=rng.choice(["A", "B"]), segment=FNO,
            )
            for i in range(30)
        ]
        result = match_fifo(trades)
        matched = sum(d.cost + d.sale_value + d.transfer_expenses for d in result.disposals)
        still_open = sum(lot.value + lot.charges for lot in result.open_lots)
        assert matched + still_open == sum(t.value + t.charges for t in trades)
        assert sum(d.stt for d in result.disposals) + sum(
            lot.stt for lot in result.open_lots) == sum(t.stt for t in trades)


def test_large_quantity_is_exact() -> None:
    result = match_fifo([
        trade("BUY", "2024-01-01", "1000000000000", "1000.1234", charges=7),
        trade("SELL", "2024-02-01", 3, 1),
    ])
    [lot] = result.open_lots
    assert result.disposals[0].cost + lot.cost == dec("1000123400000000") + 7


def test_failed_sell_leaves_book_intact() -> None:
    book = FifoBook()
    book.apply(trade("BUY", "2024-01-01", 5, 10))
    with pytest.raises(InsufficientHoldingsError):
        book.apply(trade("SELL", "2024-01-02", 6, 10))
    [lot] = book.result().open_lots
    assert lot.quantity == dec(5)
    assert not book.result().disposals


def test_opening_lots_are_sorted_by_acquisition_date() -> None:
    newer = Lot("X", date(2020, 1, 1), dec(1), dec(10), dec(0), dec(0), "NEW")
    older = Lot("X", date(2015, 1, 1), dec(1), dec(5), dec(0), dec(0), "OLD")
    result = match_fifo([trade("SELL", "2024-01-01", 1, 20, instrument="X")],
                        opening_lots=[newer, older])
    assert result.disposals[0].open_trade_id == "OLD"
    [left] = result.open_lots
    assert (left.source_trade_id, left.cost) == ("NEW", dec(10))


def test_unsquared_intraday_position_warns() -> None:
    result = match_fifo([trade("SELL", "2024-08-01", 10, 100, trade_id="S")], allow_short=True)
    [lot] = result.open_lots
    assert lot.intraday and lot.quantity == dec(-10)
    [warning] = result.warnings
    assert "not squared off" in warning
