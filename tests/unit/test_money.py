from decimal import Decimal

import pytest

from engine.money import apportion, require_decimal


def test_require_decimal() -> None:
    assert require_decimal("x", Decimal("1.5")) == Decimal("1.5")
    with pytest.raises(TypeError):
        require_decimal("x", 1.5)
    with pytest.raises(ValueError):
        require_decimal("x", Decimal("Infinity"))


def test_apportion_whole_returns_total_unchanged() -> None:
    assert apportion(Decimal("10.123456789012"), Decimal(3), Decimal(3)) == Decimal(
        "10.123456789012")


def test_apportion_is_at_internal_scale() -> None:
    assert apportion(Decimal(1), Decimal(1), Decimal(3)) == Decimal("0.3333333333")
