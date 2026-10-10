"""Settings kept in the ledger (brief 0001 task 3). Synthetic data only."""

import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from engine.api import FundClass, compute_tax_years
from engine.ledger import Batch, Ledger, LedgerError
from engine.ledger.migrations import V1
from engine.ledger.settings import ManualBuy, Settings
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import DEBT_FUND, A, buy, d, mf, sell

SALE = sell("2025-06-10", 100, 300, trade_id="Z:NSE:2025-06-10:9")
PURCHASE = ManualBuy(buy("2023-01-02", 100, 100, trade_id="MANUAL:1"), "ipo",
                     "Z:NSE:2025-06-10:9#delivery")


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger.open(tmp_path / "kosh.sqlite") as opened:
        yield opened


@pytest.fixture
def person(ledger: Ledger) -> int:
    return ledger.ensure_profile("Synthetic").id


def full_settings() -> Settings:
    return Settings(
        fund_classes={DEBT_FUND: FundClass.SPECIFIED, "INF000E01011": FundClass.EQUITY_ORIENTED},
        guessed=frozenset({"INF000E01011"}),
        fmv_2018={A: d("812.35")},
        names={A: "SYNTHETIC ALPHA LTD", "NAME:SYNTH BETA": "SYNTH BETA"},
        brought_forward=(LossEntry(2023, LossKind.SHORT_TERM_CAPITAL, d("1500.50")),
                         LossEntry(2024, LossKind.SPECULATIVE, d("200"))),
        manual_buys=(PURCHASE,),
        excluded=("Z:NSE:2025-06-10:9#delivery",),
    )


def test_settings_start_empty_and_round_trip(ledger: Ledger, person: int) -> None:
    assert ledger.settings(person) == Settings()
    ledger.import_trades(person, Batch(file_sha256="f"), [SALE])
    ledger.save_settings(person, full_settings())
    back = ledger.settings(person)
    expected = full_settings()
    assert dict(back.fund_classes) == dict(expected.fund_classes)
    assert back.guessed == expected.guessed
    assert str(back.fmv_2018[A]) == "812.35"
    assert dict(back.names) == dict(expected.names)
    assert back.brought_forward == expected.brought_forward
    assert back.manual_buys == (ManualBuy(PURCHASE.trade, "ipo", "Z:NSE:2025-06-10:9"),)
    assert back.excluded == ("Z:NSE:2025-06-10:9",)
    # Hand-entered purchases aren't imports: not in trades() or the history.
    assert [t.trade_id for t in ledger.trades(person)] == [SALE.trade_id]
    assert len(ledger.batches(person)) == 1


def test_saving_replaces_everything(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="f"), [SALE])
    ledger.save_settings(person, full_settings())
    ledger.save_settings(person, Settings(names={A: "RENAMED"}))
    assert ledger.settings(person) == Settings(names={A: "RENAMED"})


def test_exclusions_of_sales_not_saved_are_dropped(ledger: Ledger, person: int) -> None:
    ledger.save_settings(person, Settings(excluded=("GONE",)))
    assert ledger.settings(person).excluded == ()


def test_settings_are_per_profile(ledger: Ledger, person: int) -> None:
    other = ledger.ensure_profile("Other").id
    ledger.save_settings(person, Settings(fmv_2018={A: d("10")}))
    assert ledger.settings(other) == Settings()


def test_compute_uses_saved_settings(ledger: Ledger, person: int) -> None:
    """The stored purchase resolves the sale: LTCG 20,000, under the exemption."""
    ledger.import_trades(person, Batch(file_sha256="f"), [SALE])
    [incomplete] = ledger.compute(person, [2025])
    assert not incomplete.complete
    ledger.save_settings(person, Settings(manual_buys=(PURCHASE,)))
    [report] = ledger.compute(person, [2025])
    assert report.complete and report.capital_gains[0].gain == d(20000)
    ledger.save_settings(person, Settings(excluded=(SALE.trade_id,)))
    [excluded] = ledger.compute(person, [2025])
    assert excluded.complete and excluded.excluded_sales[0].trade_id == SALE.trade_id


def test_compute_matches_passing_the_same_inputs(ledger: Ledger, person: int) -> None:
    trades = [mf("BUY", "2024-05-02", 100, 10), mf("SELL", "2025-06-02", 100, 12)]
    ledger.import_trades(person, Batch(kind="cas", file_sha256="c"), trades)
    losses = (LossEntry(2024, LossKind.SHORT_TERM_CAPITAL, d("50")),)
    ledger.save_settings(person, Settings(fund_classes={DEBT_FUND: FundClass.SPECIFIED},
                                          brought_forward=losses))
    expected = compute_tax_years([2025], trades, fund_classes={DEBT_FUND: FundClass.SPECIFIED},
                                 brought_forward=losses)
    assert ledger.compute(person, [2025])[0].setoff == expected[0].setoff
    overridden = ledger.compute(person, [2025], brought_forward=())
    assert overridden[0].setoff != expected[0].setoff


def test_undo_removes_purchases_and_exclusions_for_its_sales(ledger: Ledger, person: int) -> None:
    batch = ledger.import_trades(person, Batch(file_sha256="f"), [SALE]).batch_id
    assert batch is not None
    ledger.save_settings(person, full_settings())
    ledger.undo_batch(person, batch)
    back = ledger.settings(person)
    assert (back.manual_buys, back.excluded) == ((), ())
    assert back.fmv_2018 == {A: d("812.35")}  # unrelated settings stay


def test_the_manual_batch_cannot_be_undone(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="f"), [SALE])
    ledger.save_settings(person, Settings(manual_buys=(PURCHASE,)))
    manual = ledger._db.execute("SELECT id FROM import_batch WHERE kind = 'manual'").fetchone()[0]
    with pytest.raises(LedgerError, match="hand-entered"):
        ledger.undo_batch(person, manual)
    ledger.save_settings(person, Settings())  # emptying keeps the batch, with no trades
    assert ledger.settings(person).manual_buys == ()


@pytest.mark.parametrize("bad", [
    lambda: ManualBuy(buy("2023-01-02", 1, 1, trade_id="X1"), "ipo", "S"),
    lambda: ManualBuy(sell("2023-01-02", 1, 1, trade_id="MANUAL:1"), "ipo", "S"),
    lambda: ManualBuy(buy("2023-01-02", 1, 1, trade_id="MANUAL:1"), "lottery", "S"),
    lambda: Settings(fmv_2018={A: d("0")}),
    lambda: Settings(fmv_2018={A: Decimal("NaN")}),
    lambda: Settings(guessed=frozenset({A})),
    lambda: Settings(manual_buys=(PURCHASE, PURCHASE)),
])
def test_settings_are_validated(bad: object) -> None:
    with pytest.raises(ValueError):
        bad()  # type: ignore[operator]


def test_a_version_1_ledger_is_upgraded_keeping_its_trades(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        for statement in V1.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        db.execute("INSERT INTO profile VALUES (1, 'Old', '2026-10-01')")
    db.close()
    with Ledger.open(path) as ledger:
        assert ledger.schema_version() == 2
        ledger.save_settings(1, Settings(fund_classes={DEBT_FUND: FundClass.SPECIFIED},
                                         guessed=frozenset({DEBT_FUND})))
        assert ledger.settings(1).guessed == {DEBT_FUND}


def test_exclusions_keep_the_order_they_were_made_in(ledger: Ledger, person: int) -> None:
    sales = [sell("2025-06-10", 1, 300, trade_id=f"Z:{n}") for n in ("b", "a", "c")]
    ledger.import_trades(person, Batch(file_sha256="f"), sales)
    ledger.save_settings(person, Settings(excluded=("Z:b", "Z:a")))
    ledger.save_settings(person, Settings(excluded=("Z:b", "Z:a", "Z:c")))
    assert ledger.settings(person).excluded == ("Z:b", "Z:a", "Z:c")


def test_a_setting_saved_before_its_import_takes_the_trades_kind(ledger: Ledger,
                                                                 person: int) -> None:
    etf = "INF000Z01019"
    ledger.save_settings(person, Settings(names={etf: "SYNTHETIC ETF"}))
    ledger.import_trades(person, Batch(file_sha256="f"), [buy("2025-04-02", 1, 10, instrument=etf)])
    kind = ledger._db.execute("SELECT kind FROM instrument WHERE isin = ?", (etf,)).fetchone()
    assert kind == ("EQUITY",)
    assert ledger.settings(person).names == {etf: "SYNTHETIC ETF"}
