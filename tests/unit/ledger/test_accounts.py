"""Demat accounts and transfers in the ledger (brief 0003). Synthetic data only."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger, LedgerError
from engine.ledger.migrations import V1, V2
from engine.ledger.settings import ManualBuy, Settings
from engine.ledger.store import _statements
from engine.models import Segment
from tests.golden.helpers import A, buy, d, mf, sell


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger.open(tmp_path / "kosh.sqlite") as opened:
        yield opened


@pytest.fixture
def person(ledger: Ledger) -> int:
    return ledger.ensure_profile("Synthetic").id


def test_imports_carry_their_account(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(broker="zerodha", file_sha256="z"),
                         [buy("2024-01-02", 100, 100, trade_id="Z:1")], account="Zerodha")
    ledger.import_trades(person, Batch(broker="groww", file_sha256="g"),
                         [buy("2024-06-03", 100, 200, trade_id="G:1")], account=" groww ")
    ledger.import_trades(person, Batch(kind="cas", file_sha256="c"),
                         [mf("BUY", "2024-05-02", 10, 10)], account="Zerodha")
    assert {t.trade_id: t.account for t in ledger.trades(person)} == {
        "Z:1": "Zerodha", "G:1": "groww", next(t.trade_id for t in ledger.trades(person)
                                               if t.segment is Segment.MUTUAL_FUND): None}
    assert ledger.accounts(person) == ["Zerodha", "groww"]
    ledger.import_trades(person, Batch(file_sha256="g2"),
                         [buy("2024-07-03", 1, 1, trade_id="G:2")], account="GROWW")
    assert ledger.accounts(person) == ["Zerodha", "groww"]  # same account, any case


def test_transfers_are_saved_replayed_and_removed(ledger: Ledger, person: int) -> None:
    """Golden a3 through the ledger: G's own 50 first, then 70 of the 100 moved from Z."""
    ledger.import_trades(person, Batch(file_sha256="z"),
                         [buy("2023-01-02", 100, 100, trade_id="Z:1")], account="Zerodha")
    ledger.import_trades(person, Batch(file_sha256="g"),
                         [buy("2024-06-03", 50, 300, trade_id="G:1"),
                          sell("2025-06-02", 120, 400, trade_id="G:2")], account="Groww")
    moved = ledger.add_transfer(person, date(2024, 9, 2), A, d(100), "Zerodha", "groww")
    [move] = ledger.transfers(person)
    assert (move.transfer_id, move.from_account, move.to_account, move.quantity) == (
        f"TRANSFER:{moved}", "Zerodha", "Groww", d(100))
    [report] = ledger.compute(person, [2025])
    assert [line.gain for line in report.capital_gains] == [d(5000), d(21000)]
    ledger.remove_transfer(person, moved)
    [without] = ledger.compute(person, [2025])
    assert not without.complete  # 70 of the sale have no purchase in Groww
    with pytest.raises(LedgerError, match="no transfer"):
        ledger.remove_transfer(person, moved)


@pytest.mark.parametrize(("quantity", "to", "message"), [
    (d(0), "Groww", "positive quantity"), (d(1), "zerodha", "two different accounts"),
    (d(1), " ", "needs a name")])
def test_bad_transfers_are_refused(ledger: Ledger, person: int, quantity: object, to: str,
                                   message: str) -> None:
    with pytest.raises(LedgerError, match=message):
        ledger.add_transfer(person, date(2024, 9, 2), A, quantity, "Zerodha", to)  # type: ignore[arg-type]


def test_rename_keeps_trades_and_transfers(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="z"),
                         [buy("2023-01-02", 100, 100, trade_id="Z:1")], account="GROWWINDIA")
    ledger.add_transfer(person, date(2024, 9, 2), A, d(10), "GROWWINDIA", "Zerodha")
    ledger.rename_account(person, "GROWWINDIA", "Groww")
    assert ledger.trades(person)[0].account == "Groww"
    assert ledger.transfers(person)[0].from_account == "Groww"
    with pytest.raises(LedgerError, match="already an account"):
        ledger.rename_account(person, "Groww", "zerodha")
    with pytest.raises(LedgerError, match="no account"):
        ledger.rename_account(person, "Nope", "X")
    with pytest.raises(LedgerError, match="needs a name"):
        ledger.rename_account(person, "Groww", "  ")


def test_unassigned_trades_can_be_put_in_an_account(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(kind="opening"),
                         [buy("2016-04-01", 10, 100, trade_id="OPENING:x:1")])
    ledger.import_trades(person, Batch(kind="cas", file_sha256="c"),
                         [mf("BUY", "2024-05-02", 1, 1)])
    assert ledger.trades(person)[0].account is None
    assert ledger.assign_account(person, "Zerodha") == 1  # the fund units keep no account
    assert ledger.trades(person)[0].account == "Zerodha"


def test_hand_entered_purchases_keep_their_account(ledger: Ledger, person: int) -> None:
    sale = replace(sell("2025-06-10", 100, 300, trade_id="Z:9"), account="Zerodha")
    ledger.import_trades(person, Batch(file_sha256="z"), [sale])
    purchase = ManualBuy(replace(buy("2023-01-02", 100, 100, trade_id="MANUAL:1"),
                                 account="Zerodha"), "ipo", "Z:9")
    ledger.save_settings(person, Settings(manual_buys=(purchase,)))
    assert ledger.settings(person).manual_buys[0].trade.account == "Zerodha"
    assert ledger.compute(person, [2025])[0].complete


def test_opening_lots_keep_the_date_they_entered(ledger: Ledger, person: int) -> None:
    lot = replace(buy("2016-04-01", 10, 100, trade_id="OPENING:y:1"),
                  entered_on=date(2020, 1, 2), account="Groww")
    ledger.import_trades(person, Batch(kind="opening"), [lot])
    assert ledger.trades(person) == [lot]


def version_2_ledger(path: Path, *, accounts: int) -> None:
    with sqlite3.connect(path) as db:
        for sql in (V1, V2):
            for statement in _statements(sql):
                db.execute(statement)
        db.execute("INSERT INTO meta VALUES ('schema_version', '2')")
        db.execute("INSERT INTO profile VALUES (1, 'Old', '2026-10-01')")
        db.execute("INSERT INTO instrument (id, isin, kind) VALUES (1, ?, 'EQUITY')", (A,))
        brokers = ["zerodha", "GROWW"][:accounts]
        for n, broker in enumerate(brokers, start=1):
            db.execute("INSERT INTO import_batch (id, profile_id, kind, broker, imported_at)"
                       " VALUES (?, 1, 'tradebook', ?, '2026-10-01')", (n, broker))
            db.execute("INSERT INTO trade (profile_id, batch_id, instrument_id, source_id,"
                       " segment, trade_date, side, quantity, price, dedupe_key) VALUES"
                       " (1, ?, 1, ?, 'EQUITY', '2024-01-02', 'BUY', '1', '1', ?)",
                       (n, f"T{n}", f"k{n}"))
        db.execute("INSERT INTO import_batch (id, profile_id, kind, imported_at)"
                   " VALUES (9, 1, 'opening', '2026-10-01')")
        db.execute("INSERT INTO trade (profile_id, batch_id, instrument_id, source_id,"
                   " segment, trade_date, side, quantity, price, dedupe_key) VALUES"
                   " (1, 9, 1, 'OPENING:o', 'EQUITY', '2016-01-04', 'BUY', '1', '1', 'ko')")
    db.close()


def test_upgrading_puts_saved_trades_in_broker_accounts(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite"
    version_2_ledger(path, accounts=1)
    with Ledger.open(path) as ledger:
        assert {t.trade_id: t.account for t in ledger.trades(1)} == {
            "T1": "Zerodha", "OPENING:o": "Zerodha"}  # the only account takes the opening lot


def test_upgrading_with_two_brokers_leaves_opening_lots_unassigned(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite"
    version_2_ledger(path, accounts=2)
    with Ledger.open(path) as ledger:
        assert {t.trade_id: t.account for t in ledger.trades(1)} == {
            "T1": "Zerodha", "T2": "GROWW", "OPENING:o": None}
        assert ledger.accounts(1) == ["Zerodha", "GROWW"]


def test_account_names_ignore_case_spaces_and_punctuation(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [buy("2024-01-02", 1, 1, trade_id="I:1")],
                         account="ICICIDIRECT")
    ledger.import_trades(person, Batch(file_sha256="b"), [buy("2024-01-03", 1, 1, trade_id="I:2")],
                         account="ICICI Direct")
    assert ledger.accounts(person) == ["ICICIDIRECT"]
    assert ledger.account_name(person, "icici-direct") == "ICICIDIRECT"
    assert ledger.account_name(person, "Groww") is None
    with pytest.raises(LedgerError, match="letter or digit"):
        ledger.import_trades(person, Batch(file_sha256="c"),
                             [buy("2024-01-04", 1, 1, trade_id="I:3")], account="--")
    ledger.rename_account(person, "icici direct", "ICICI Direct")  # same account, new spelling
    assert ledger.accounts(person) == ["ICICI Direct"]
    with pytest.raises(LedgerError, match="letter or digit"):
        ledger.rename_account(person, "ICICI Direct", "...")


def test_assigning_some_trades_and_rekeying_opening_lots(ledger: Ledger, person: int) -> None:
    lots = [buy("2016-04-01", 50, 100, trade_id="OPENING:a:1"),
            buy("2017-04-03", 20, 90, trade_id="OPENING:b:1")]
    ledger.import_trades(person, Batch(kind="opening"), lots)
    assert ledger.assign_account(person, "Zerodha", ["OPENING:a:1"]) == 1
    assert {t.trade_id: t.account for t in ledger.trades(person)} == {
        "OPENING:a:1": "Zerodha", "OPENING:b:1": None}
    # The lot now in Zerodha is recognised when entered again for Zerodha…
    again = ledger.import_trades(person, Batch(kind="opening"), [lots[0]], account="Zerodha")
    assert (again.added, again.duplicates) == (0, 1)
    # …and the same purchase in another account is a second lot.
    other = ledger.import_trades(person, Batch(kind="opening"), [lots[0]], account="Groww")
    assert other.added == 1


def test_which_account_a_file_went_into(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="f"), [buy("2024-01-02", 1, 1, trade_id="Z:1")],
                         account="Zerodha")
    assert ledger.imported_into(person, "f") == "Zerodha"
    assert ledger.imported_into(person, "nope") is None


def test_upgrade_rekeys_opening_lots_with_their_account(tmp_path: Path) -> None:
    """An opening lot saved under v2 (key without account) lands in the only account during
    the upgrade; entering it again for that account is recognised as the same lot."""
    path = tmp_path / "v2.sqlite"
    version_2_ledger(path, accounts=1)
    with Ledger.open(path) as ledger:
        lot = replace(buy("2016-01-04", 1, 1, trade_id="OPENING:o2:1"), account="Zerodha")
        again = ledger.import_trades(1, Batch(kind="opening"), [lot])
        assert (again.added, again.duplicates) == (0, 1)
