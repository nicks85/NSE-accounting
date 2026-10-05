from datetime import date

import pytest

from engine.matching.corporate_actions import Bonus, Split
from engine.matching.fifo import match_fifo
from engine.models import Lot
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
    assert not result.warnings


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
    assert "fractional" in result.warnings[0]


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
