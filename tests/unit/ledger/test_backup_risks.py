"""QA probes for task 5 restore risks. Synthetic data only."""

import sqlite3
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger
from tests.golden.helpers import buy


def _one_trade(path: Path, name: str) -> bytes:
    with Ledger.open(path) as ledger:
        me = ledger.ensure_profile(name).id
        ledger.import_trades(me, Batch(file_sha256=name), [buy("2024-05-02", 10, 100,
                                                                trade_id=f"Z:{name}")])
        return ledger.backup()


def test_second_restore_keeps_the_original_data(tmp_path: Path) -> None:
    a = _one_trade(tmp_path / "a.sqlite", "A")
    b = _one_trade(tmp_path / "b.sqlite", "B")
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        ledger.ensure_profile("Original")
        ledger.restore(a)
        ledger.restore(b)  # user picked the wrong file first
    found = []
    for p in tmp_path.glob("kosh.sqlite.before-restore*"):
        with Ledger.open(p) as old:
            found += [x.display_name for x in old.profiles()]
    assert "Original" in found


def test_a_backup_with_a_trigger_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "evil.sqlite"
    _one_trade(src, "E")
    with sqlite3.connect(src) as db:
        db.execute("CREATE TRIGGER t AFTER INSERT ON trade BEGIN DELETE FROM trade; END")
    db.close()
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        try:
            ledger.restore(src.read_bytes())
        except Exception:
            return
        me = ledger.profiles()[0].id
        ledger.import_trades(me, Batch(file_sha256="n"), [buy("2024-06-02", 1, 1, trade_id="Z:n")])
        assert len(ledger.trades(me)) == 2


def test_a_failure_part_way_leaves_the_data_and_no_temp_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The copy-in fails (disk full, or a file Windows won't release): the ledger keeps its
    data, keeps working, and no ``.restoring`` file is left behind."""
    a = _one_trade(tmp_path / "a.sqlite", "A")
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        ledger.ensure_profile("Original")

        def boom() -> set[tuple[str, str, str, str]]:
            raise sqlite3.OperationalError("disk I/O error")
        monkeypatch.setattr("engine.ledger.store._expected_schema", boom)
        with pytest.raises(sqlite3.OperationalError):
            ledger.restore(a)
        assert [p.display_name for p in ledger.profiles()] == ["Original"]
        ledger.ensure_profile("Still writable")
    assert not (tmp_path / "kosh.sqlite.restoring").exists()


def test_restore_while_another_process_has_it_open(tmp_path: Path) -> None:
    a = _one_trade(tmp_path / "a.sqlite", "A")
    path = tmp_path / "kosh.sqlite"
    with Ledger.open(path) as ledger, Ledger.open(path) as other:
        ledger.ensure_profile("Original")
        ledger.restore(a)
        other.ensure_profile("Written by window 2 after restore")
        names = [p.display_name for p in ledger.profiles()]
    assert "Written by window 2 after restore" in names
