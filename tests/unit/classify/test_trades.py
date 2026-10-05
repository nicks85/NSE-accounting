from engine.classify.trades import classify_trades
from engine.matching.fifo import match_fifo
from engine.models import Segment, Side
from tests.factories import dec, trade


def _qty(trades: tuple, side: Side) -> list:
    return [t.quantity for t in trades if t.side is side]


def test_pure_delivery_and_fno_pass_through() -> None:
    buy = trade("BUY", "2024-01-01", 10, 100)
    sell = trade("SELL", "2024-03-01", 10, 120)
    fut = trade("BUY", "2024-01-01", 25, 22000, instrument="NIFTYFUT", segment=Segment.FNO)
    result = classify_trades([buy, sell, fut])
    assert result.delivery == (buy, sell)
    assert result.intraday == ()
    assert result.fno == (fut,)


def test_same_day_round_trip_is_intraday() -> None:
    result = classify_trades([trade("BUY", "2024-08-01", 10, 100),
                              trade("SELL", "2024-08-01", 10, 101)])
    assert result.delivery == ()
    assert _qty(result.intraday, Side.BUY) == [dec(10)]
    assert _qty(result.intraday, Side.SELL) == [dec(10)]


def test_partial_square_off_leaves_delivery_remainder_with_exact_charges() -> None:
    result = classify_trades([
        trade("BUY", "2024-08-01", 30, 100, charges=3, stt=3, trade_id="B"),
        trade("SELL", "2024-08-01", 10, 105, charges=1, trade_id="S"),
    ])
    [intra_buy, intra_sell] = result.intraday
    [carry] = result.delivery
    assert (intra_buy.quantity, intra_sell.quantity, carry.quantity) == (dec(10), dec(10), dec(20))
    assert intra_buy.charges + carry.charges == dec(3)
    assert intra_buy.stt + carry.stt == dec(3)
    assert carry.side is Side.BUY and carry.trade_id == "B#delivery"


def test_intraday_units_come_from_earliest_trades_each_side() -> None:
    result = classify_trades([
        trade("BUY", "2024-08-01", 5, 100, trade_id="B1"),
        trade("BUY", "2024-08-01", 5, 102, trade_id="B2"),
        trade("SELL", "2024-08-01", 7, 103, trade_id="S1"),
    ])
    assert [(t.trade_id, t.quantity) for t in result.intraday] == [
        ("B1", dec(5)), ("B2#intraday", dec(2)), ("S1", dec(7))]
    assert [(t.trade_id, t.quantity) for t in result.delivery] == [("B2#delivery", dec(3))]


def test_existing_holding_does_not_absorb_intraday_pair() -> None:
    """QA scenario: hold an old lot, buy and sell the same day — the pair is intraday."""
    trades = [
        trade("BUY", "2022-01-03", 10, 50, trade_id="OLD"),
        trade("BUY", "2024-08-01", 10, 100, trade_id="IB"),
        trade("SELL", "2024-08-01", 10, 101, trade_id="IS"),
    ]
    result = classify_trades(trades)
    delivery = match_fifo(result.delivery)
    assert delivery.disposals == ()
    assert [lot.source_trade_id for lot in delivery.open_lots] == ["OLD"]
    [pair] = match_fifo(result.intraday, allow_short=True).disposals
    assert pair.gain == dec(10)


def test_intraday_short_sell_listed_first_matches() -> None:
    """QA scenario: MIS short in cash equity (sell, then buy back the same day)."""
    result = classify_trades([trade("SELL", "2024-08-01", 10, 100),
                              trade("BUY", "2024-08-01", 10, 99)])
    assert result.delivery == ()
    matched = match_fifo(result.intraday, allow_short=True)
    [pair] = matched.disposals
    assert pair.gain == dec(10) and not matched.open_lots


def test_net_sell_against_holding_is_delivery() -> None:
    result = classify_trades([
        trade("BUY", "2024-01-01", 20, 100),
        trade("BUY", "2024-08-01", 5, 110, trade_id="B2"),
        trade("SELL", "2024-08-01", 15, 112, trade_id="S1"),
    ])
    assert [(t.trade_id, t.quantity) for t in result.intraday] == [
        ("B2", dec(5)), ("S1#intraday", dec(5))]
    [_, carry_sell] = result.delivery
    assert (carry_sell.side, carry_sell.quantity) == (Side.SELL, dec(10))


def test_different_scrips_same_day_are_not_netted() -> None:
    result = classify_trades([
        trade("BUY", "2024-08-01", 10, 100, instrument="A"),
        trade("SELL", "2024-08-01", 10, 100, instrument="B"),
    ])
    assert result.intraday == ()


def test_no_intraday_means_no_warning() -> None:
    assert classify_trades([trade("BUY", "2024-01-01", 1, 1)]).warnings == ()


def test_intraday_surfaces_unverified_warning() -> None:
    result = classify_trades([trade("BUY", "2024-08-01", 1, 1), trade("SELL", "2024-08-01", 1, 2)])
    [warning] = result.warnings
    assert "UNVERIFIED" in warning and "Q-004" in warning


def test_split_pieces_have_unique_ids_and_conserve_uneven_amounts() -> None:
    result = classify_trades([
        trade("BUY", "2024-08-01", 3, 100, charges=1, stt=1, trade_id="B"),
        trade("SELL", "2024-08-01", 1, 101, trade_id="S"),
    ])
    pieces = result.intraday + result.delivery
    ids = [t.trade_id for t in pieces]
    assert len(ids) == len(set(ids))
    buys = [t for t in pieces if t.side is Side.BUY]
    assert sum(t.quantity for t in buys) == dec(3)
    assert sum(t.charges for t in buys) == dec(1)
    assert sum(t.stt for t in buys) == dec(1)


def test_days_are_netted_separately() -> None:
    result = classify_trades([
        trade("BUY", "2024-08-01", 10, 100),
        trade("SELL", "2024-08-02", 10, 101),
    ])
    assert result.intraday == ()


def test_buy_sell_buy_same_day_last_buy_is_delivery() -> None:
    result = classify_trades([
        trade("BUY", "2024-08-01", 5, 100, trade_id="B1"),
        trade("SELL", "2024-08-01", 5, 101, trade_id="S1"),
        trade("BUY", "2024-08-01", 5, 102, trade_id="B2"),
    ])
    assert [t.trade_id for t in result.intraday] == ["B1", "S1"]
    assert [t.trade_id for t in result.delivery] == ["B2"]


def test_intraday_short_against_existing_holding_keeps_holding() -> None:
    result = classify_trades([
        trade("BUY", "2022-01-01", 100, 50, trade_id="OLD"),
        trade("SELL", "2024-08-01", 50, 100, trade_id="S"),
        trade("BUY", "2024-08-01", 50, 99, trade_id="B"),
    ])
    delivery = match_fifo(result.delivery)
    [lot] = delivery.open_lots
    assert (lot.source_trade_id, lot.quantity) == ("OLD", dec(100))
    assert not delivery.disposals
