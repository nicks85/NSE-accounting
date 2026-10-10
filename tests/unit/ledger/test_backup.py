"""Backup and restore (brief 0001 task 5). Synthetic data only."""

import sqlite3
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger, LedgerError
from engine.ledger.migrations import LATEST, V1
from engine.ledger.settings import Settings
from tests.golden.helpers import A, buy, d, sell


def filled(path: Path) -> Ledger:
    ledger = Ledger.open(path)
    me = ledger.ensure_profile("Me").id
    other = ledger.ensure_profile("Second").id
    ledger.import_trades(me, Batch(file_sha256="a", file_name="a.csv"),
                         [buy("2024-05-02", 10, 100, trade_id="Z:1")])
    ledger.import_trades(other, Batch(file_sha256="b"), [sell("2025-06-01", 1, 1, trade_id="Z:9")])
    ledger.save_settings(me, Settings(fmv_2018={A: d("812.35")}, filed_on_time={2024: False}))
    return ledger


def test_backup_restores_every_person_exactly(tmp_path: Path) -> None:
    with filled(tmp_path / "old" / "kosh.sqlite") as source:
        data = source.backup()
        before = [source.trades(p.id) for p in source.profiles()]
    with Ledger.open(tmp_path / "new" / "kosh.sqlite") as target:
        target.ensure_profile("Someone else")
        kept = target.restore(data)
        assert [p.display_name for p in target.profiles()] == ["Me", "Second"]
        assert [target.trades(p.id) for p in target.profiles()] == before
        settings = target.settings(target.profiles()[0].id)
        assert str(settings.fmv_2018[A]) == "812.35" and settings.filed_on_time == {2024: False}
        assert target.batches(1)[0]["file_name"] == "a.csv"
    assert kept.name.startswith("kosh.sqlite.before-restore-")
    with Ledger.open(kept) as previous:
        assert [p.display_name for p in previous.profiles()] == ["Someone else"]
    assert not (tmp_path / "new" / "kosh.sqlite.restoring").exists()


def test_a_backup_is_a_plain_sqlite_file(tmp_path: Path) -> None:
    with filled(tmp_path / "kosh.sqlite") as source:
        data = source.backup()
    assert data.startswith(b"SQLite format 3\x00")


@pytest.mark.parametrize(("data", "message"), [
    (b"not a database at all" * 200, "not an SQLite file"),
    (b"", "not an SQLite file"),
])
def test_bad_backups_are_refused_and_nothing_changes(tmp_path: Path, data: bytes,
                                                     message: str) -> None:
    with filled(tmp_path / "kosh.sqlite") as ledger:
        with pytest.raises(LedgerError, match=message):
            ledger.restore(data)
        assert [p.display_name for p in ledger.profiles()] == ["Me", "Second"]
        assert len(ledger.trades(1)) == 1
    assert not list(tmp_path.glob("kosh.sqlite.before-restore*"))
    assert not (tmp_path / "kosh.sqlite.restoring").exists()


def foreign_or_newer(tmp_path: Path, newer: bool) -> bytes:
    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as db:
        if newer:
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(LATEST + 1),))
        else:
            db.execute("CREATE TABLE notes (x)")
    db.close()
    return path.read_bytes()


@pytest.mark.parametrize(("newer", "message"), [(True, "newer version of Kosh"),
                                                 (False, "has no Kosh schema version")])
def test_foreign_and_newer_backups_are_refused(tmp_path: Path, newer: bool,
                                               message: str) -> None:
    data = foreign_or_newer(tmp_path, newer)
    with filled(tmp_path / "kosh.sqlite") as ledger:
        with pytest.raises(LedgerError, match=message):
            ledger.restore(data)
        assert len(ledger.profiles()) == 2


def test_a_damaged_backup_is_refused(tmp_path: Path) -> None:
    with filled(tmp_path / "source.sqlite") as source:
        data = bytearray(source.backup())
    page = int.from_bytes(data[16:18], "big")  # page size, from the SQLite header
    data[page:] = b"\xff" * (len(data) - page)  # every page after the first
    with filled(tmp_path / "kosh.sqlite") as ledger:
        with pytest.raises(LedgerError, match=r"damaged|not a Kosh ledger"):
            ledger.restore(bytes(data))
        assert len(ledger.profiles()) == 2


def test_an_older_backup_is_upgraded(tmp_path: Path) -> None:
    old = tmp_path / "v1.sqlite"
    with sqlite3.connect(old) as db:
        for statement in V1.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        db.execute("INSERT INTO profile VALUES (1, 'From v1', '2026-10-01')")
    db.close()
    empty_meta = tmp_path / "empty-meta.sqlite"
    with sqlite3.connect(empty_meta) as db:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.close()
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        with pytest.raises(LedgerError, match="has no Kosh schema version"):
            ledger.restore(empty_meta.read_bytes())
        ledger.restore(old.read_bytes())
        assert ledger.schema_version() == LATEST
        assert [p.display_name for p in ledger.profiles()] == ["From v1"]


def test_a_ledger_without_a_file_cannot_restore() -> None:
    db = sqlite3.connect(":memory:")
    with pytest.raises(LedgerError, match="no file"):
        Ledger(db).restore(b"")
    db.close()


@pytest.mark.parametrize(("error", "message"), [("database is locked", "busy in another Kosh"),
                                                 ("disk I/O error", "restore failed: disk I/O")])
def test_copy_errors_are_reported_and_the_data_kept(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch,
                                                    error: str, message: str) -> None:
    with filled(tmp_path / "source.sqlite") as source:
        data = source.backup()
    calls = []

    def failing(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
        calls.append(1)
        if len(calls) == 2:  # the copy-in, after the before-restore copy
            raise sqlite3.OperationalError(error)
        src.backup(dst)
    monkeypatch.setattr("engine.ledger.store._copy", failing)
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        ledger.ensure_profile("Original")
        with pytest.raises(LedgerError, match=message):
            ledger.restore(data)
        assert [p.display_name for p in ledger.profiles()] == ["Original"]
    assert not (tmp_path / "kosh.sqlite.restoring").exists()


def test_two_restores_in_one_second_keep_both_copies(tmp_path: Path) -> None:
    with filled(tmp_path / "source.sqlite") as source:
        data = source.backup()
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        first, second = ledger.restore(data), ledger.restore(data)
    assert first != second and first.exists() and second.exists()


def test_a_backup_with_broken_links_is_refused(tmp_path: Path) -> None:
    """QA: a trade pointing at a person who isn't there would vanish silently."""
    doctored = tmp_path / "doctored.sqlite"
    filled(doctored).close()
    with sqlite3.connect(doctored) as db:
        db.execute("UPDATE trade SET profile_id = 99 WHERE id = 1")
    db.close()
    with Ledger.open(tmp_path / "kosh.sqlite") as ledger:
        ledger.ensure_profile("Original")
        with pytest.raises(LedgerError, match="point at records that aren't there"):
            ledger.restore(doctored.read_bytes())
        assert [p.display_name for p in ledger.profiles()] == ["Original"]
