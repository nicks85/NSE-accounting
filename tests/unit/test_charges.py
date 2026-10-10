"""Charges by type (brief 0005). Synthetic data only."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from engine.api import charges_by_type
from engine.ledger import Batch, Ledger
from engine.models import Trade
from tests.golden.helpers import buy, d, sell

PARTS = (("BROKERAGE", d("20")), ("GST", d("3.6")), ("STAMP", d("15")))


def priced(trade: Trade, parts: tuple[tuple[str, Decimal], ...] = PARTS) -> Trade:
    return replace(trade, charges=sum((a for _, a in parts), Decimal(0)), charge_parts=parts)


def test_charges_by_type_must_add_up_and_be_known() -> None:
    lot = buy("2025-04-02", 10, 100, trade_id="Z:1")
    assert priced(lot).charges == d("38.6")
    with pytest.raises(ValueError, match="don't add up"):
        replace(lot, charges=d(1), charge_parts=PARTS)
    with pytest.raises(ValueError, match="distinct"):
        replace(lot, charges=d(2), charge_parts=(("TIPS", d(2)),))
    with pytest.raises(ValueError, match="distinct"):
        replace(lot, charges=d(2), charge_parts=(("GST", d(1)), ("GST", d(1))))


def test_slices_of_a_trade_carry_no_breakdown_and_equality_ignores_it() -> None:
    whole = priced(buy("2025-04-02", 10, 100, trade_id="Z:1"))
    head, rest = whole.split(d(4))
    assert head.charge_parts == () and rest is not None and rest.charge_parts == ()
    assert head.charges + rest.charges == whole.charges
    assert whole.portion(d(10), "#x").charge_parts == ()
    assert whole.split(d(10))[0].charge_parts == ()
    assert whole == replace(whole, charge_parts=())  # information only


def test_the_ledger_keeps_charges_by_type(tmp_path: Path) -> None:
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.ensure_profile("Synthetic").id
        typed = priced(buy("2025-04-02", 10, 100, trade_id="Z:1"))
        total = replace(buy("2025-04-03", 10, 100, trade_id="Z:2"), charges=d(5))
        only_other = priced(buy("2025-04-04", 10, 100, trade_id="Z:3"), (("OTHER", d(2)),))
        ledger.import_trades(person, Batch(file_sha256="a"), [typed, total, only_other])
        back = {t.trade_id: t for t in ledger.trades(person)}
    assert back["Z:1"].charge_parts == PARTS and back["Z:1"].charges == d("38.6")
    assert back["Z:2"].charge_parts == () and back["Z:2"].charges == d(5)
    assert back["Z:3"].charge_parts == () and back["Z:3"].charges == d(2)  # not broken down


def test_charges_by_type_for_a_year() -> None:
    trades = [
        priced(buy("2025-04-02", 10, 100, stt=10, trade_id="A:1")),
        priced(sell("2025-06-02", 10, 120, stt=12, trade_id="A:2"), (("BROKERAGE", d(20)),)),
        replace(buy("2025-07-01", 1, 1, trade_id="M:1"), charges=d(5)),  # not broken down
        buy("2025-08-01", 1, 1, trade_id="ZERODHA:NSE:1"),  # no charges in the file
        buy("2025-08-02", 1, 1, trade_id="ZERODHA:NSE:2"),
        priced(buy("2024-04-02", 10, 100, trade_id="A:0")),  # another year
    ]
    summary = charges_by_type(trades, 2025)
    assert dict(summary.by_kind) == {
        "BROKERAGE": (d(20), d(20)), "GST": (d("3.6"), d(0)), "STAMP": (d(15), d(0)),
        "STT": (d(10), d(12)), "Not broken down": (d(5), d(0))}
    assert list(summary.by_kind) == ["BROKERAGE", "GST", "STAMP", "STT", "Not broken down"]
    assert dict(summary.without_charges) == {"ZERODHA": 2}
    assert charges_by_type(trades, 2026).by_kind == {}
    stt_only = [buy("2025-05-05", 1, 1, stt=2, trade_id="A:9")]  # STT, no other charges
    assert dict(charges_by_type(stt_only, 2025).by_kind) == {"STT": (d(2), d(0))}


def test_year_bounds_are_inclusive() -> None:
    edge = [priced(buy("2026-03-31", 1, 1, trade_id="E:1")),
            priced(buy("2025-04-01", 1, 1, trade_id="E:2"))]
    assert charges_by_type(edge, 2025).by_kind["BROKERAGE"] == (d(40), d(0))


def test_angel_one_rows_read_back_exactly_as_imported(tmp_path: Path) -> None:
    from importers.angel_one import load_angel_one_tradebooks
    from tests.fixtures import angel_one as ao

    row = ao.Row("SYNTHETIC ALPHA LTD", "BUY", "100", 10, "2025-05-02", "1", brokerage="5",
                 gst="0.9", stamp="1.5", exchange_charges="0.3", sebi="0.01")
    [trade] = load_angel_one_tradebooks([("a.csv", ao.trades_csv([row]))]).result.trades
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.ensure_profile("Synthetic").id
        ledger.import_trades(person, Batch(file_sha256="a"), [trade])
        [back] = ledger.trades(person)
    assert back.charge_parts == trade.charge_parts
    assert [k for k, _ in trade.charge_parts] == ["BROKERAGE", "GST", "EXCHANGE", "SEBI", "STAMP"]
