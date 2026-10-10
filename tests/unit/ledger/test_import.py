"""Importing into the ledger: duplicates, conflicts, re-imports and undo (brief 0001 D3,
task 2). Synthetic data only."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger, LedgerError, dedupe_keys
from engine.models import Segment, Side, Trade
from tests.golden.helpers import A, B, buy, d, sell


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger.open(tmp_path / "kosh.sqlite") as opened:
        yield opened


@pytest.fixture
def person(ledger: Ledger) -> int:
    return ledger.ensure_profile("Synthetic").id


def cas(on: str, units: str, nav: str, index: int, side: Side = Side.BUY) -> Trade:
    return Trade(f"CAS:INF000E01011:{on}:{index}", date.fromisoformat(on), "INF000E01011", side,
                 d(units), d(nav), segment=Segment.MUTUAL_FUND)


def test_ensure_profile_creates_once(ledger: Ledger) -> None:
    first = ledger.ensure_profile(" Me ")
    assert ledger.ensure_profile("Me") == first
    assert len(ledger.profiles()) == 1


def test_overlapping_tradebooks_add_only_new_trades(ledger: Ledger, person: int) -> None:
    april = [buy("2025-04-02", 10, 100, trade_id="Z:NSE:2025-04-02:1"),
             buy("2025-04-03", 5, 101, trade_id="Z:NSE:2025-04-03:2")]
    first = ledger.import_trades(person, Batch(file_sha256="apr", file_name="apr.csv"), april,
                                 warnings=["note"])
    assert (first.added, first.duplicates, first.conflicts) == (2, 0, ())
    later = [*april, sell("2025-05-02", 15, 120, trade_id="Z:NSE:2025-05-02:3")]
    second = ledger.import_trades(person, Batch(file_sha256="apr-may"), later)
    assert (second.added, second.duplicates) == (1, 2)
    assert [t.trade_id for t in ledger.trades(person)] == [
        "Z:NSE:2025-04-02:1", "Z:NSE:2025-04-03:2", "Z:NSE:2025-05-02:3"]
    history = ledger.batches(person)
    assert [(b["trades_added"], b["duplicates_skipped"], b["rows_read"]) for b in history] == [
        (2, 0, 2), (1, 2, 3)]
    assert (history[1]["date_from"], history[1]["date_to"]) == ("2025-04-02", "2025-05-02")


def test_same_file_again_is_recognised_before_reading(ledger: Ledger, person: int) -> None:
    trades = [buy("2025-04-02", 10, 100, trade_id="Z1")]
    ledger.import_trades(person, Batch(file_sha256="f"), trades)
    again = ledger.import_trades(person, Batch(file_sha256="f"), trades)
    assert again.batch_id is None and again.already_imported_on is not None
    assert len(ledger.batches(person)) == 1


def test_nothing_new_stores_no_batch(ledger: Ledger, person: int) -> None:
    trades = [buy("2025-04-02", 10, 100, trade_id="Z1")]
    ledger.import_trades(person, Batch(file_sha256="a"), trades)
    outcome = ledger.import_trades(person, Batch(file_sha256="b"), trades)
    assert (outcome.batch_id, outcome.added, outcome.duplicates) == (None, 0, 1)
    assert len(ledger.batches(person)) == 1


def test_same_id_different_details_refuses_the_whole_file(ledger: Ledger, person: int) -> None:
    original = buy("2025-04-02", 10, 100, trade_id="Z1")
    ledger.import_trades(person, Batch(file_sha256="a"), [original])
    changed = replace(original, price=d("101"))
    outcome = ledger.import_trades(
        person, Batch(file_sha256="b"), [buy("2025-04-03", 1, 1, trade_id="NEW"), changed])
    assert outcome.batch_id is None and outcome.added == 0
    [conflict] = outcome.conflicts
    assert (conflict.new.price, conflict.existing.price) == (d("101"), d("100"))
    assert [t.trade_id for t in ledger.trades(person)] == ["Z1"]


def test_conflict_inside_one_file(ledger: Ledger, person: int) -> None:
    one = buy("2025-04-02", 10, 100, trade_id="Z1")
    outcome = ledger.import_trades(person, Batch(), [one, replace(one, quantity=d("11"))])
    assert len(outcome.conflicts) == 1 and ledger.trades(person) == []


def test_amounts_written_differently_are_the_same_trade(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [buy("2025-04-02", 10, "100.50",
                                                              trade_id="Z1")])
    outcome = ledger.import_trades(person, Batch(file_sha256="b"), [
        replace(buy("2025-04-02", 10, "100.5000", trade_id="Z1"), quantity=d("10.0"))])
    assert (outcome.duplicates, outcome.conflicts) == (1, ())


def test_cas_keys_survive_a_longer_statement(ledger: Ledger, person: int) -> None:
    """A later CAS covering more history numbers the same transactions differently; they must
    still be recognised. Two identical purchases on one day stay two."""
    short = [cas("2024-05-02", "10", "50", 0), cas("2024-05-02", "10", "50", 1)]
    long = [cas("2023-01-02", "5", "40", 0), cas("2024-05-02", "10", "50.0", 1),
            cas("2024-05-02", "10", "50", 2), cas("2024-05-02", "10", "50", 3)]
    ledger.import_trades(person, Batch(kind="cas", file_sha256="short"), short)
    outcome = ledger.import_trades(person, Batch(kind="cas", file_sha256="long"), long)
    assert (outcome.added, outcome.duplicates) == (2, 2)
    assert len(ledger.trades(person)) == 4
    assert dedupe_keys(short)[0] != dedupe_keys(short)[1]
    assert dedupe_keys([buy("2025-04-02", 1, 1, trade_id="Z9")]) == ["EQUITY|Z9"]


def test_undo_removes_trades_and_allows_reimport(ledger: Ledger, person: int) -> None:
    trades = [buy("2025-04-02", 10, 100, trade_id="Z1"), buy("2025-04-03", 1, 100,
                                                             instrument=B, trade_id="Z2")]
    batch = ledger.import_trades(person, Batch(file_sha256="f"), trades).batch_id
    assert batch is not None
    assert ledger.undo_batch(person, batch) == 2
    assert ledger.trades(person) == [] and ledger.batches(person) == []
    with pytest.raises(LedgerError, match="already undone"):
        ledger.undo_batch(person, batch)
    again = ledger.import_trades(person, Batch(file_sha256="f"), trades)
    assert again.added == 2 and {t.instrument for t in ledger.trades(person)} == {A, B}


def test_undo_checks_the_profile(ledger: Ledger, person: int) -> None:
    other = ledger.ensure_profile("Other").id
    batch = ledger.import_trades(person, Batch(), [buy("2025-04-02", 1, 1)]).batch_id
    assert batch is not None
    with pytest.raises(LedgerError, match="no import"):
        ledger.undo_batch(other, batch)


def test_import_checks_kind_and_profile(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="unknown batch kind"):
        ledger.import_trades(1, Batch(kind="x"), [])
    with pytest.raises(LedgerError, match="no profile"):
        ledger.import_trades(42, Batch(), [])


def test_same_timed_trade_from_another_source_is_refused(ledger: Ledger, person: int) -> None:
    at = datetime(2025, 4, 2, 9, 30, 5)
    ledger.import_trades(person, Batch(file_sha256="a"), [
        replace(buy("2025-04-02", 10, 100, trade_id="GROWW:NSE:2025-04-02:1"), executed_at=at)])
    twin = replace(buy("2025-04-02", 10, 100, trade_id="GROWWINDIA:NSE:2025-04-02:1"),
                   executed_at=at)
    outcome = ledger.import_trades(person, Batch(file_sha256="b"), [twin])
    assert [c.reason for c in outcome.conflicts] == ["other_source"]
    # Identical fills from the same source are separate trades, and untimed ones aren't checked.
    same_source = replace(twin, trade_id="GROWW:NSE:2025-04-02:2")
    untimed = replace(twin, executed_at=None)
    assert ledger.import_trades(person, Batch(file_sha256="c"), [same_source, untimed]).added == 2


def test_ensure_profile_needs_a_name(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="needs a name"):
        ledger.ensure_profile(" ")


def test_untimed_lookalikes_from_another_source_are_saved_with_a_warning(
        ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [
        buy("2025-04-02", 10, 100, trade_id="GROWW:NSE:2025-04-02:1")])
    outcome = ledger.import_trades(person, Batch(file_sha256="b"), [
        buy("2025-04-02", 10, 100, trade_id="GROWWINDIA:NSE:2025-04-02:1"),
        buy("2025-04-02", 10, 100, trade_id="GROWW:NSE:2025-04-02:2")])
    assert outcome.added == 2
    assert [c.new.trade_id for c in outcome.possible_duplicates] == ["GROWWINDIA:NSE:2025-04-02:1"]


def test_how_acquired_is_stored_with_the_trade(ledger: Ledger, person: int) -> None:
    lot = buy("2016-04-01", 10, 100, trade_id="OPENING:INE000A01012:2016-04-01:1")
    ledger.import_trades(person, Batch(kind="opening"), [lot], how_acquired={lot.trade_id: "gift"})
    assert ledger._db.execute("SELECT how_acquired FROM trade").fetchone() == ("gift",)
    assert dedupe_keys([lot])[0].startswith("H|")  # same lot entered twice is one lot


def test_a_corrected_opening_lot_is_a_conflict_not_a_duplicate(ledger: Ledger,
                                                                person: int) -> None:
    lot = buy("2016-04-01", 10, 100, trade_id="OPENING:INE000A01012:2016-04-01:10:100:1")
    ledger.import_trades(person, Batch(kind="opening"), [lot],
                         how_acquired={lot.trade_id: "bought"})
    same = ledger.import_trades(person, Batch(kind="opening"), [lot],
                                how_acquired={lot.trade_id: "bought"})
    assert (same.duplicates, same.conflicts) == (1, ())
    as_gift = ledger.import_trades(person, Batch(kind="opening"), [lot],
                                   how_acquired={lot.trade_id: "gift"})
    with_charges = ledger.import_trades(person, Batch(kind="opening"),
                                        [replace(lot, charges=d("12"))])
    assert len(as_gift.conflicts) == 1 and len(with_charges.conflicts) == 1
    assert len(ledger.trades(person)) == 1
