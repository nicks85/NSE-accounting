"""Ledger store (brief 0001 task 1). Synthetic data only."""

import sqlite3
import subprocess
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from engine.api import compute_tax_years
from engine.ledger import (
    Batch,
    Ledger,
    LedgerError,
    data_dir,
    decimal_text,
    ledger_path,
    text_decimal,
)
from engine.ledger.migrations import LATEST
from engine.models import Segment, Side, Trade
from tests.golden.helpers import FUT, A, B, buy, d, fut, sell

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger.open(tmp_path / "kosh.sqlite") as opened:
        yield opened


@pytest.mark.parametrize("text", ["0", "0.00", "-0", "1E+2", "12345678901234567890.123456789",
                                  "0.0000001", "100.50", "-3.10"])
def test_decimal_text_round_trip_keeps_digits_and_exponent(text: str) -> None:
    value = Decimal(text)
    back = text_decimal(decimal_text(value))
    assert back == value and back.as_tuple() == value.as_tuple()


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), 1.5, "1"])
def test_decimal_text_refuses_non_decimals(bad: object) -> None:
    with pytest.raises(LedgerError):
        decimal_text(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["abc", "NaN", "-Infinity"])
def test_text_decimal_refuses_bad_text(bad: str) -> None:
    with pytest.raises(LedgerError):
        text_decimal(bad)


def test_new_file_gets_latest_schema_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "kosh.sqlite"
    with Ledger.open(path) as ledger:
        assert ledger.schema_version() == LATEST
        ledger.create_profile("  Synthetic Person  ")
    with Ledger.open(path) as again:
        [profile] = again.profiles()
        assert profile.display_name == "Synthetic Person"
        assert again.schema_version() == LATEST


def test_newer_schema_is_refused_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "kosh.sqlite"
    Ledger.open(path).close()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(LATEST + 1),))
    db.close()
    with pytest.raises(LedgerError, match="newer version of Kosh"):
        Ledger.open(path)
    with sqlite3.connect(path) as db:
        version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    db.close()
    assert version == (str(LATEST + 1),)


def test_foreign_files_are_refused(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite"
    with sqlite3.connect(other) as db:
        db.execute("CREATE TABLE notes (x)")
    db.close()
    with pytest.raises(LedgerError, match="no Kosh schema version"):
        Ledger.open(other)
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"this is not a database" * 100)
    with pytest.raises(LedgerError, match="not a Kosh ledger"):
        Ledger.open(junk)


def test_profiles_need_a_name_and_trades_a_known_profile(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="needs a name"):
        ledger.create_profile("  ")
    with pytest.raises(LedgerError, match="no profile 99"):
        ledger.trades(99)
    with pytest.raises(LedgerError, match="no profile 99"):
        ledger.add_batch(99, Batch(), [])


def test_trades_round_trip_exactly(ledger: Ledger) -> None:
    person = ledger.create_profile("Synthetic").id
    trades = [
        Trade("ZERODHA:NSE:2025-06-02:1", date(2025, 6, 2), A, Side.BUY, d("10"),
              d("1234.5000"), charges=d("12.34"), stt=d("1.2345"),
              executed_at=datetime(2025, 6, 2, 9, 15, 3)),
        Trade("CAS:INF000:2025-06-03:0", date(2025, 6, 3), "INF000E01011", Side.BUY,
              d("12.345"), d("45.6789"), segment=Segment.MUTUAL_FUND),
        fut("SELL", "2025-06-04", 50, 25000),
        sell("2025-06-05", 4, "1300.25", trade_id="S1"),
    ]
    batch = ledger.add_batch(person, Batch("tradebook", "zerodha", "tb.csv", "ab" * 32), trades)
    assert ledger.trades(person) == trades
    back = ledger.trades(person)[0]
    assert (str(back.price), str(back.charges), str(back.stt)) == ("1234.5000", "12.34", "1.2345")
    [row] = ledger.batches(person)
    assert row["id"] == batch and row["broker"] == "zerodha"
    assert (row["date_from"], row["date_to"], row["trades_added"]) == (
        "2025-06-02", "2025-06-05", 4)


def test_profiles_are_kept_apart(ledger: Ledger) -> None:
    one, two = ledger.create_profile("One").id, ledger.create_profile("Two").id
    ledger.add_batch(one, Batch(), [buy("2025-04-02", 1, 10)])
    ledger.add_batch(two, Batch(), [buy("2025-04-02", 2, 10, instrument=B)])
    assert [t.instrument for t in ledger.trades(one)] == [A]
    assert [t.instrument for t in ledger.trades(two)] == [B]


def test_a_batch_is_all_or_nothing(ledger: Ledger) -> None:
    person = ledger.create_profile("Synthetic").id
    first = buy("2025-04-02", 1, 10, trade_id="X1")
    ledger.add_batch(person, Batch(file_sha256="f1"), [first])
    with pytest.raises(LedgerError, match="already in the ledger"):
        ledger.add_batch(person, Batch(file_sha256="f2"),
                         [buy("2025-04-03", 1, 10, trade_id="NEW"), first])
    with pytest.raises(LedgerError, match="already in the ledger"):
        ledger.add_batch(person, Batch(file_sha256="f1"), [])
    assert [t.trade_id for t in ledger.trades(person)] == ["X1"]
    assert len(ledger.batches(person)) == 1


def test_batch_arguments_are_checked(ledger: Ledger) -> None:
    person = ledger.create_profile("Synthetic").id
    with pytest.raises(LedgerError, match="unknown batch kind"):
        ledger.add_batch(person, Batch(kind="email"), [])
    with pytest.raises(LedgerError, match="one dedupe key"):
        ledger.add_batch(person, Batch(), [buy("2025-04-02", 1, 10)], keys=[])


def test_same_instrument_across_batches_and_profiles_shares_one_row(ledger: Ledger) -> None:
    one, two = ledger.create_profile("One").id, ledger.create_profile("Two").id
    ledger.add_batch(one, Batch(), [buy("2024-04-02", 1, 10), fut("BUY", "2025-06-02", 1, 1)])
    ledger.add_batch(two, Batch(), [buy("2024-04-03", 1, 10)])
    assert {t.instrument for t in ledger.trades(one)} == {A, FUT}


def test_replay_matches_computing_from_the_files(ledger: Ledger) -> None:
    """Two years from two imports: the stored ledger gives exactly what the trades give."""
    person = ledger.create_profile("Synthetic").id
    year_one = [buy("2023-05-02", 100, 100), sell("2024-06-03", 40, 150),
                buy("2024-07-01", 10, 50, instrument=B), sell("2024-08-01", 10, 30, instrument=B)]
    year_two = [sell("2025-06-02", 60, 200), fut("BUY", "2025-07-01", 50, 100),
                fut("SELL", "2025-07-10", 50, 90)]
    ledger.add_batch(person, Batch(file_sha256="y1"), year_one)
    ledger.add_batch(person, Batch(file_sha256="y2"), year_two)
    expected = compute_tax_years([2024, 2025], year_one + year_two)
    replayed = ledger.compute(person, [2024, 2025])
    assert [r.setoff for r in replayed] == [r.setoff for r in expected]
    assert [r.capital_gains for r in replayed] == [r.capital_gains for r in expected]
    assert replayed[1].open_lots == expected[1].open_lots == ()


def test_replay_of_a_large_history_is_fast(ledger: Ledger) -> None:
    """D2 assumed replay costs milliseconds; 20,000 trades must load and compute well under
    the time a person waits for a screen (measured figure recorded in brief 0001)."""
    person = ledger.create_profile("Synthetic").id
    start, trades = date(2019, 4, 1), []
    for n in range(10_000):
        on = start + timedelta(days=n // 5)
        instrument = f"INE{n % 50:03d}A01011"
        trades.append(buy(on.isoformat(), 10, 100 + n % 7, instrument=instrument,
                          trade_id=f"B{n}"))
        trades.append(sell((on + timedelta(days=400)).isoformat(), 10, 120,
                           instrument=instrument, trade_id=f"S{n}"))
    ledger.add_batch(person, Batch(), trades)
    began = time.perf_counter()
    reports = ledger.compute(person, range(2024, 2027))  # history from 2019 is replayed
    elapsed = time.perf_counter() - began
    assert len(reports) == 3 and reports[0].capital_gains
    print(f"replayed {len(trades)} trades for 3 years in {elapsed:.2f}s")
    assert elapsed < 30, f"replay took {elapsed:.1f}s"


def test_data_dir_per_platform(tmp_path: Path) -> None:
    home = Path("/home/x")
    assert data_dir({}, "darwin", home) == home / "Library/Application Support/in.kosh.app"
    assert data_dir({"APPDATA": "C:/Users/x/AppData/Roaming"}, "win32", home) == Path(
        "C:/Users/x/AppData/Roaming/in.kosh.app")
    assert data_dir({}, "win32", home) == home / "AppData/Roaming/in.kosh.app"
    assert data_dir({}, "linux", home) == home / ".local/share/in.kosh.app"
    assert data_dir({"XDG_DATA_HOME": "/data"}, "linux", home) == Path("/data/in.kosh.app")
    assert data_dir({"KOSH_DATA_DIR": str(tmp_path)}, "darwin", home) == tmp_path
    assert ledger_path({"KOSH_DATA_DIR": str(tmp_path)}) == tmp_path / "kosh.sqlite"


def test_no_ledger_file_is_ever_committed() -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,  # noqa: S607
                             check=True).stdout.split()
    assert not [f for f in tracked if f.endswith((".sqlite", ".db", ".kosh"))]
    ignore = (ROOT / ".gitignore").read_text()
    assert "*.sqlite" in ignore and "*.kosh" in ignore


def test_a_folder_is_not_a_ledger(tmp_path: Path) -> None:
    with pytest.raises(LedgerError, match="cannot open"):
        Ledger.open(tmp_path)


def test_migration_statements_split_only_between_statements() -> None:
    from engine.ledger.store import _statements

    assert _statements("CREATE TABLE a (x TEXT DEFAULT ';');\nINSERT INTO a VALUES ('b;c');\n") == [
        "CREATE TABLE a (x TEXT DEFAULT ';');", "INSERT INTO a VALUES ('b;c');"]
    with pytest.raises(LedgerError, match="incomplete migration statement"):
        _statements("CREATE TABLE a (x TEXT")
