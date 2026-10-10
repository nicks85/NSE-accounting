"""QA review findings for the ledger store (brief 0001 task 1). Synthetic data only."""

import sqlite3
import threading
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger, LedgerError
from engine.models import Side, Trade
from tests.golden.helpers import A, buy, d, sell


def test_zero_charges_and_aware_timestamps_round_trip(tmp_path: Path) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    trades = [
        Trade("T1", date(2025, 6, 2), A, Side.BUY, d("1"), d("10"), charges=d("0.00"),
              stt=d("0.000"), executed_at=datetime(2025, 6, 2, 9, 15, 3, 250, tzinfo=ist)),
        Trade("T2", date(2025, 6, 2), A, Side.SELL, d("1"), d("10"), charges=d("0.50"),
              executed_at=datetime(2025, 6, 2, 4, 0, tzinfo=UTC)),
    ]
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.create_profile("Synthetic").id
        ledger.add_batch(person, Batch(), trades)
        back = ledger.trades(person)
    assert back == trades
    assert back[0].executed_at is not None and back[0].executed_at.utcoffset() == timedelta(
        hours=5, minutes=30)
    assert back[1].stt == 0 and back[1].charges == d("0.50")


def test_same_day_trades_from_two_batches_replay_in_execution_order(tmp_path: Path) -> None:
    """Importers order by (date, executed_at, file position) (importers/tabular.py:87). Two
    batches touching the same day (two brokers, or an overlapping later file) must replay in
    the same order the importers would give, or FIFO and intraday netting change."""
    late = buy("2025-06-02", 1, 20, trade_id="LATE")
    late = Trade(late.trade_id, late.trade_date, late.instrument, late.side, late.quantity,
                 late.price, executed_at=datetime(2025, 6, 2, 15, 0))
    early = buy("2025-06-02", 1, 10, trade_id="EARLY")
    early = Trade(early.trade_id, early.trade_date, early.instrument, early.side,
                  early.quantity, early.price, executed_at=datetime(2025, 6, 2, 9, 30))
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.create_profile("Synthetic").id
        ledger.add_batch(person, Batch(file_sha256="a"), [late])
        ledger.add_batch(person, Batch(file_sha256="b"), [early])
        assert [t.trade_id for t in ledger.trades(person)] == ["EARLY", "LATE"]


def test_a_file_can_be_reimported_after_its_batch_is_undone(tmp_path: Path) -> None:
    """Brief schema: undo = set undone_at and drop the batch's trades; the batch row stays,
    so the file's hash blocks a re-import for ever. Cheap to fix now, before v1 ships."""
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.create_profile("Synthetic").id
        batch = ledger.add_batch(person, Batch(file_sha256="f"), [buy("2025-04-02", 1, 10)])
        ledger._db.execute("DELETE FROM trade WHERE batch_id = ?", (batch,))
        ledger._db.execute("UPDATE import_batch SET undone_at = 'x' WHERE id = ?", (batch,))
        ledger.add_batch(person, Batch(file_sha256="f"), [buy("2025-04-02", 1, 10)])


def test_two_processes_creating_the_ledger_at_once_both_succeed(tmp_path: Path) -> None:
    path = tmp_path / "k.sqlite"
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def open_it() -> None:
        try:
            barrier.wait()
            Ledger.open(path).close()
        except BaseException as error:
            errors.append(error)

    for _ in range(20):
        path.unlink(missing_ok=True)
        threads = [threading.Thread(target=open_it) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []


def test_a_locked_file_is_not_reported_as_foreign(tmp_path: Path) -> None:
    """A busy ledger (another Kosh process mid-write) must not be called 'not a Kosh ledger'."""
    path = tmp_path / "k.sqlite"
    Ledger.open(path).close()
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(LedgerError) as caught:
            Ledger.open(path)
        assert "not a Kosh ledger" not in str(caught.value)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_failed_batch_leaves_no_orphan_instrument(tmp_path: Path) -> None:
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.create_profile("Synthetic").id
        first = sell("2025-04-02", 1, 10, trade_id="X")
        ledger.add_batch(person, Batch(), [first])
        with pytest.raises(LedgerError):
            ledger.add_batch(person, Batch(), [buy("2025-04-02", 1, 10, instrument="INE999Z01011"),
                                               first])
        assert ledger._db.execute("SELECT COUNT(*) FROM instrument").fetchone() == (1,)
        assert ledger._db.execute("SELECT COUNT(*) FROM import_batch").fetchone() == (1,)
