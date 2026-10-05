from datetime import date
from decimal import Decimal

import pytest

from engine.models import Lot, Segment, Side, Trade
from tests.factories import dec, trade


def test_trade_rejects_float_amounts() -> None:
    with pytest.raises(TypeError, match="price must be Decimal"):
        Trade("T", date(2024, 1, 1), "X", Side.BUY, dec(1), 10.5)  # type: ignore[arg-type]


def test_trade_rejects_non_finite_and_bad_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        Trade("T", date(2024, 1, 1), "X", Side.BUY, dec(1), Decimal("NaN"))
    with pytest.raises(ValueError, match="quantity must be positive"):
        trade("BUY", "2024-01-01", 0, 10)
    with pytest.raises(ValueError, match="non-negative"):
        trade("BUY", "2024-01-01", 1, 10, charges=-1)


def test_trade_portion_apportions_charges() -> None:
    t = trade("BUY", "2024-01-01", 3, 100, charges=10, stt=3)
    part = t.portion(dec(1), "a")
    assert part.trade_id.endswith("a")
    assert (part.quantity, part.charges, part.stt) == (dec(1), Decimal("3.3333333333"), dec(1))
    with pytest.raises(ValueError, match="invalid portion"):
        t.portion(dec(4), "b")


def test_lot_take_conserves_amounts_exactly() -> None:
    lot = Lot("X", date(2024, 1, 1), dec(3), dec(100), dec(10), dec(1), "T1")
    taken, rest = lot.take(dec(1))
    assert rest is not None
    assert taken.value + rest.value == dec(100)
    assert taken.charges + rest.charges == dec(10)
    assert taken.stt + rest.stt == dec(1)
    assert lot.cost == dec(110)
    whole, none = lot.take(dec(3))
    assert whole is lot and none is None
    with pytest.raises(ValueError, match="invalid take"):
        lot.take(dec(5))


def test_lot_rejects_zero_quantity() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        Lot("X", date(2024, 1, 1), dec(0), dec(0), dec(0), dec(0), "T1")


def test_lot_rejects_short_equity_and_negative_amounts() -> None:
    with pytest.raises(ValueError, match="cannot be short"):
        Lot("X", date(2024, 1, 1), dec(-1), dec(10), dec(0), dec(0), "T1")
    with pytest.raises(ValueError, match="non-negative"):
        Lot("X", date(2024, 1, 1), dec(1), dec(-10), dec(0), dec(0), "T1")
    short = Lot("F", date(2024, 1, 1), dec(-1), dec(10), dec(0), dec(0), "T1", Segment.FNO)
    with pytest.raises(ValueError, match="no cost"):
        _ = short.cost


def test_short_equity_lot_allowed_only_when_intraday() -> None:
    lot = Lot("X", date(2024, 1, 1), dec(-1), dec(10), dec(0), dec(0), "T1", intraday=True)
    assert not lot.is_long
    with pytest.raises(ValueError, match="cannot be short"):
        Lot("X", date(2024, 1, 1), dec(-1), dec(10), dec(0), dec(0), "T1", intraday=False)


def test_trade_split_whole_and_suffixes() -> None:
    t = trade("BUY", "2024-01-01", 3, 10, trade_id="T")
    whole, none = t.split(dec(3), "#a", "#b")
    assert whole.trade_id == "T#a" and none is None
    head, rest = t.split(dec(1), "#a", "#b")
    assert rest is not None and (head.trade_id, rest.trade_id) == ("T#a", "T#b")
