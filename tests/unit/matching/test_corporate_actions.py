from datetime import date

import pytest

from engine.matching.corporate_actions import Bonus, Split
from engine.matching.fifo import match_fifo
from engine.models import Lot, Segment
from tests.factories import dec, trade

ISIN = "INE000A01011"


def test_split_multiplies_quantity_keeps_cost_and_date() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 10, 1000), trade("SELL", "2024-03-01", 50, 250)],
        actions=[Split(ISIN, date(2023, 6, 1), old=1, new=5)],
    )
    [d] = result.disposals
    assert (d.quantity, d.cost, d.sale_value, d.acquired_on) == (
        dec(50), dec(10000), dec(12500), date(2022, 1, 10))
    [warning] = result.warnings
    assert "UNVERIFIED" in warning and "Q-002" in warning


def test_split_applies_before_trades_on_ex_date() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 10, 1000), trade("BUY", "2023-06-01", 5, 200)],
        actions=[Split(ISIN, date(2023, 6, 1), old=1, new=5)],
    )
    assert [lot.quantity for lot in result.open_lots] == [dec(50), dec(5)]


def test_consolidation_with_fraction_warns() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 7, 10)],
        actions=[Split(ISIN, date(2023, 6, 1), old=2, new=1)],
    )
    [lot] = result.open_lots
    assert lot.quantity == dec("3.5")
    assert "fractional" in result.warnings[1]


def test_bonus_creates_zero_cost_lot_from_allotment_date() -> None:
    result = match_fifo(
        [
            trade("BUY", "2022-01-10", 10, 1000),
            trade("BUY", "2022-05-10", 5, 1100),
            trade("SELL", "2024-03-01", 30, 600),
        ],
        actions=[Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1,
                       allotment_date=date(2023, 6, 3))],
    )
    first, second, bonus = result.disposals
    assert (first.quantity, second.quantity, bonus.quantity) == (dec(10), dec(5), dec(15))
    assert bonus.cost == 0 and bonus.acquired_on == date(2023, 6, 3)
    assert bonus.open_trade_id == "BONUS:INE000A01011:2023-06-01"
    assert not result.open_lots


def test_bonus_defaults_to_ex_date_and_drops_fraction() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 5, 100)],
        actions=[Bonus(ISIN, date(2023, 6, 1), held=2, bonus=1)],
    )
    _, bonus = result.open_lots
    assert (bonus.quantity, bonus.acquired_on) == (dec(2), date(2023, 6, 1))
    assert "fractional entitlement 0.5" in result.warnings[0]


def test_bonus_with_no_holding_creates_nothing() -> None:
    result = match_fifo([], actions=[Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1)])
    assert not result.open_lots and not result.warnings


def test_bonus_counts_opening_lots() -> None:
    opening = Lot(ISIN, date(2015, 1, 1), dec(4), dec(400), dec(0), dec(0), "OPEN")
    result = match_fifo([], opening_lots=[opening],
                        actions=[Bonus(ISIN, date(2017, 6, 1), held=1, bonus=1)])
    assert [lot.quantity for lot in result.open_lots] == [dec(4), dec(4)]


def test_invalid_ratios_rejected() -> None:
    with pytest.raises(ValueError):
        Split(ISIN, date(2023, 1, 1), old=0, new=1)
    with pytest.raises(ValueError):
        Bonus(ISIN, date(2023, 1, 1), held=1, bonus=0)


def test_consolidation_is_exact_without_false_fraction_warning() -> None:
    result = match_fifo([trade("BUY", "2022-01-10", 21, 10)],
                        actions=[Split(ISIN, date(2023, 6, 1), old=7, new=3)])
    [lot] = result.open_lots
    assert lot.quantity == dec(9) and str(lot.quantity) == "9"
    assert len(result.warnings) == 1  # only the UNVERIFIED notice


def test_fraction_check_uses_aggregate_holding() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 3, 10), trade("BUY", "2022-02-10", 5, 10)],
        actions=[Split(ISIN, date(2023, 6, 1), old=2, new=1)],
    )
    assert sum(lot.quantity for lot in result.open_lots) == dec(4)
    assert len(result.warnings) == 1


def test_corporate_actions_ignore_fno_lots() -> None:
    fut = trade("SELL", "2023-05-01", 50, 100, instrument=ISIN, segment=Segment.FNO)
    result = match_fifo([fut], actions=[Split(ISIN, date(2023, 6, 1), old=1, new=5),
                                        Bonus(ISIN, date(2023, 7, 1), held=1, bonus=1)])
    [lot] = result.open_lots
    assert lot.quantity == dec(-50)


def test_buy_on_ex_date_is_matched_before_later_dated_bonus_lot() -> None:
    result = match_fifo(
        [
            trade("BUY", "2023-01-01", 10, 100, trade_id="OLD"),
            trade("BUY", "2023-06-01", 5, 50, trade_id="EX"),
            trade("SELL", "2024-08-01", 15, 60),
        ],
        actions=[Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1,
                       allotment_date=date(2023, 6, 3))],
    )
    assert [d.open_trade_id for d in result.disposals] == ["OLD", "EX"]
    [bonus] = result.open_lots
    assert bonus.source_trade_id.startswith("BONUS:")


def test_split_then_bonus() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 10, 1000)],
        actions=[Split(ISIN, date(2023, 1, 1), old=1, new=2),
                 Bonus(ISIN, date(2023, 6, 1), held=2, bonus=1)],
    )
    assert [lot.quantity for lot in result.open_lots] == [dec(20), dec(10)]
    assert result.open_lots[0].cost == dec(10000)


def test_partly_sold_lot_before_bonus() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 10, 100), trade("SELL", "2023-03-01", 4, 120)],
        actions=[Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1)],
    )
    assert [lot.quantity for lot in result.open_lots] == [dec(6), dec(6)]


def test_sale_before_ex_date_is_not_entitled() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 10, 100), trade("SELL", "2023-05-31", 10, 120)],
        actions=[Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1)],
    )
    assert not result.open_lots


def test_split_with_no_holding_only_warns_unverified() -> None:
    result = match_fifo([], actions=[Split(ISIN, date(2023, 1, 1), old=1, new=2)])
    assert not result.open_lots and len(result.warnings) == 1


def test_two_actions_same_ex_date_apply_in_input_order() -> None:
    result = match_fifo(
        [trade("BUY", "2022-01-10", 10, 100)],
        actions=[Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1),
                 Split(ISIN, date(2023, 6, 1), old=1, new=2)],
    )
    assert [lot.quantity for lot in result.open_lots] == [dec(20), dec(20)]


def test_corporate_actions_skip_intraday_lots() -> None:
    intraday = Lot(ISIN, date(2023, 6, 1), dec(10), dec(100), dec(0), dec(0), "I",
                   intraday=True)
    result = match_fifo([], opening_lots=[intraday],
                        actions=[Split(ISIN, date(2023, 6, 1), old=1, new=2),
                                 Bonus(ISIN, date(2023, 6, 1), held=1, bonus=1)])
    [lot] = [lot for lot in result.open_lots if lot.source_trade_id == "I"]
    assert lot.quantity == dec(10)
    assert not [lot for lot in result.open_lots if lot.source_trade_id.startswith("BONUS")]


def test_ex_date_intraday_round_trip_with_split() -> None:
    from engine.classify.trades import classify_trades

    classified = classify_trades([
        trade("BUY", "2023-01-01", 10, 100),
        trade("BUY", "2023-06-01", 5, 50),
        trade("SELL", "2023-06-01", 5, 52),
    ])
    split = [Split(ISIN, date(2023, 6, 1), old=1, new=2)]
    delivery = match_fifo(classified.delivery, actions=split)
    assert [lot.quantity for lot in delivery.open_lots] == [dec(20)]
    intraday = match_fifo(classified.intraday, allow_short=True)
    [pair] = intraday.disposals
    assert pair.gain == dec(10) and not intraday.open_lots
