"""Company names to ISINs (brief 0007, option a). Synthetic data only."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger, LedgerError
from engine.ledger.migrations import V1, V2, V3
from engine.ledger.settings import ManualBuy, Settings
from engine.ledger.store import _statements
from tests.golden.helpers import buy, d, sell

ISIN = "INE000A01012"  # check-digit valid, synthetic
NAME = "EXAMPLE DEPO SER (I)"
KEY = f"NAME:{NAME}"


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger.open(tmp_path / "k.sqlite") as opened:
        yield opened


@pytest.fixture
def person(ledger: Ledger) -> int:
    return ledger.ensure_profile("Synthetic").id


def angel(on: str, side: str, n: int, price: int, number: str) -> object:
    make = buy if side == "BUY" else sell
    return replace(make(on, n, price, instrument=KEY, trade_id=f"ANGELONE:NSE:{on}:{number}"),
                   account="Angel One")


def test_mapping_a_name_moves_trades_settings_and_transfers(ledger: Ledger, person: int) -> None:
    trades = [angel("2025-04-02", "BUY", 10, 100, "1"), angel("2025-06-02", "SELL", 4, 120, "2")]
    ledger.import_trades(person, Batch(broker="angelone", file_sha256="a"), trades)
    ledger.save_settings(person, Settings(names={KEY: NAME}, fmv_2018={KEY: d(50)}))
    ledger.add_transfer(person, date(2025, 5, 1), KEY, d(1), "Angel One", "Zerodha")
    assert ledger.unmapped_names(person) == [(NAME, 2)]
    assert ledger.map_name(person, "angelone", NAME, " ine000a01012 ") == 2
    assert {t.instrument for t in ledger.trades(person)} == {ISIN}
    assert ledger.unmapped_names(person) == [] and ledger.mapped_names(person) == [(NAME, ISIN)]
    settings = ledger.settings(person)
    assert settings.names[ISIN] == NAME and settings.fmv_2018 == {ISIN: d(50)}
    assert KEY not in settings.names
    assert ledger.transfers(person)[0].instrument == ISIN
    assert ledger.aliases(person, "angelone") == {NAME: ISIN}


def test_a_reimport_with_the_isin_is_a_duplicate_not_a_conflict(ledger: Ledger,
                                                                 person: int) -> None:
    """The conflict found in task 2: same trade id, instrument now ISIN."""
    trades = [angel("2025-04-02", "BUY", 10, 100, "1")]
    ledger.import_trades(person, Batch(file_sha256="a"), trades)
    ledger.map_name(person, "angelone", NAME, ISIN)
    again = ledger.import_trades(person, Batch(file_sha256="b"),
                                 [replace(t, instrument=ISIN) for t in trades])  # type: ignore[type-var]
    assert (again.duplicates, again.conflicts) == (1, ())


def test_two_names_for_one_company_end_up_together(ledger: Ledger, person: int) -> None:
    other = replace(angel("2025-04-03", "BUY", 1, 1, "3"), instrument="NAME:EXAMPLE DEPOSITORY")
    ledger.import_trades(person, Batch(file_sha256="a"),
                         [angel("2025-04-02", "BUY", 10, 100, "1"), other])
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.map_name(person, "angelone", "EXAMPLE DEPOSITORY", ISIN)
    assert {t.instrument for t in ledger.trades(person)} == {ISIN}
    assert sorted(n for n, _ in ledger.mapped_names(person)) == sorted(
        ["EXAMPLE DEPOSITORY", NAME])


def test_undo_puts_the_trades_back_under_the_name(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-04-02", "BUY", 10, 100, "1")])
    ledger.add_transfer(person, date(2025, 5, 1), KEY, d(1), "Angel One", "Zerodha")
    ledger.map_name(person, "angelone", NAME, ISIN)
    assert ledger.unmap_name(person, "angelone", NAME) == 1
    assert [t.instrument for t in ledger.trades(person)] == [KEY]
    assert ledger.transfers(person)[0].instrument == KEY
    assert ledger.aliases(person, "angelone") == {} and ledger.mapped_names(person) == []


def test_undo_keeps_shared_transfers_when_the_isin_has_other_trades(ledger: Ledger,
                                                                   person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2025-04-02", "BUY", 10, 100, "1"),
        replace(buy("2025-04-03", 5, 100, instrument=ISIN, trade_id="Z:1"), account="Zerodha")])
    ledger.add_transfer(person, date(2025, 5, 1), ISIN, d(1), "Zerodha", "Angel One")
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.transfers(person)[0].instrument == ISIN  # Zerodha's shares still use it


def test_a_name_with_no_trades_is_still_remembered(ledger: Ledger, person: int) -> None:
    assert ledger.map_name(person, "angelone", "NEVER SEEN", ISIN) == 0
    assert ledger.aliases(person, "angelone") == {"NEVER SEEN": ISIN}


@pytest.mark.parametrize(("name", "isin", "message"), [
    (NAME, "INE000A01011", "isn't a valid Indian ISIN"), (" ", ISIN, "company name is needed")])
def test_bad_mappings_are_refused(ledger: Ledger, person: int, name: str, isin: str,
                                  message: str) -> None:
    with pytest.raises(LedgerError, match=message):
        ledger.map_name(person, "angelone", name, isin)


def test_a_name_is_compared_as_the_importer_writes_it(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-04-02", "BUY", 10, 100, "1")])
    assert ledger.map_name(person, "angelone", "  example depo  ser (i) ", ISIN) == 1
    assert ledger.mapped_names(person) == [(NAME, ISIN)]
    assert ledger.unmap_name(person, "angelone", " Example Depo Ser (I)") == 1


def test_a_name_is_matched_once(ledger: Ledger, person: int) -> None:
    ledger.map_name(person, "angelone", NAME, ISIN)
    assert ledger.map_name(person, "angelone", NAME, ISIN) == 0  # the same answer again
    with pytest.raises(LedgerError, match="already matched to INE000A01012; undo that first"):
        ledger.map_name(person, "angelone", NAME, "INE000A01020")


def test_undo_of_a_name_never_matched_is_refused(ledger: Ledger, person: int) -> None:
    instruments = ledger._db.execute("SELECT COUNT(*) FROM instrument").fetchone()
    with pytest.raises(LedgerError, match="isn't matched to an ISIN"):
        ledger.unmap_name(person, "angelone", "NEVER SEEN")
    assert ledger._db.execute("SELECT COUNT(*) FROM instrument").fetchone() == instruments


def test_hand_entered_purchases_are_not_counted_as_imported(ledger: Ledger,
                                                            person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-06-02", "SELL", 4, 120, "2")])
    manual = ManualBuy(buy("2020-01-01", 4, 50, instrument=KEY, trade_id="MANUAL:1"), "bought",
                       "ANGELONE:NSE:2025-06-02:2")
    ledger.save_settings(person, Settings(manual_buys=(manual,)))
    assert ledger.unmapped_names(person) == [(NAME, 1)]


def test_undo_with_a_shared_isin_gives_the_name_its_own_settings(ledger: Ledger,
                                                                 person: int) -> None:
    """Another account holds the ISIN: the name gets back what it had; the ISIN keeps its own
    settings, including a price entered after the match (it may be about either holding)."""
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2017-04-02", "BUY", 10, 100, "1"),
        replace(buy("2017-04-03", 5, 100, instrument=ISIN, trade_id="Z:1"), account="Zerodha")])
    ledger.save_settings(person, Settings(names={KEY: "Name's own", ISIN: "Zerodha's"},
                                          fmv_2018={KEY: d(50)}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    assert ledger.settings(person).fmv_2018 == {ISIN: d(50)}
    ledger.save_settings(person, replace(ledger.settings(person), fmv_2018={ISIN: d(55)}))
    ledger.unmap_name(person, "angelone", NAME)
    settings = ledger.settings(person)
    assert settings.names == {KEY: "Name's own", ISIN: "Zerodha's"}
    assert settings.fmv_2018 == {KEY: d(50), ISIN: d(55)}


def test_undo_when_only_the_name_used_the_isin(ledger: Ledger, person: int) -> None:
    """Changes made on the ISIN since go to the name; the ISIN's own settings come back."""
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2017-04-02", "BUY", 10, 100, "1")])
    ledger.save_settings(person, Settings(names={ISIN: "Set before"}, fmv_2018={KEY: d(50)}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.save_settings(person, replace(ledger.settings(person), fmv_2018={ISIN: d(55)}))
    ledger.add_transfer(person, date(2025, 5, 1), ISIN, d(1), "Angel One", "Zerodha")
    ledger.unmap_name(person, "angelone", NAME)
    settings = ledger.settings(person)
    assert settings.fmv_2018 == {KEY: d(55)} and settings.names == {ISIN: "Set before"}
    assert ledger.transfers(person)[0].instrument == KEY  # recorded after the match


def test_upgrading_keeps_the_name_of_trades_imported_by_name(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite"
    with sqlite3.connect(path) as db:
        for sql in (V1, V2, V3):
            for statement in _statements(sql):
                db.execute(statement)
        db.execute("INSERT INTO meta VALUES ('schema_version', '3')")
        db.execute("INSERT INTO profile VALUES (1, 'Old', '2026-10-01')")
        db.execute("INSERT INTO instrument (id, isin, kind) VALUES (1, ?, 'EQUITY'),"
                   " (2, ?, 'EQUITY')", (KEY, ISIN))
        db.execute("INSERT INTO import_batch (id, profile_id, kind, broker, imported_at)"
                   " VALUES (1, 1, 'tradebook', 'angelone', '2026-10-01')")
        for n, instrument in ((1, 1), (2, 2)):
            db.execute("INSERT INTO trade (profile_id, batch_id, instrument_id, source_id,"
                       " segment, trade_date, side, quantity, price, dedupe_key) VALUES"
                       " (1, 1, ?, ?, 'EQUITY', '2024-01-02', 'BUY', '1', '1', ?)",
                       (instrument, f"T{n}", f"k{n}"))
    db.close()
    with Ledger.open(path) as ledger:
        assert ledger.map_name(1, "angelone", NAME, ISIN) == 1
        assert ledger.unmap_name(1, "angelone", NAME) == 1  # the ISIN's own trade stays
        assert {t.trade_id: t.instrument for t in ledger.trades(1)} == {"T1": KEY, "T2": ISIN}


def test_undo_follows_the_names_shares_through_a_chain_of_transfers(ledger: Ledger,
                                                                    person: int) -> None:
    """Angel One holds only the name's shares and passes them on to Groww, which passes them to
    Upstox: both transfers carry the name's shares. Zerodha's own transfer stays on the ISIN."""
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2025-04-02", "BUY", 10, 100, "1"),
        replace(buy("2025-04-03", 5, 100, instrument=ISIN, trade_id="Z:1"), account="Zerodha")])
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.add_transfer(person, date(2025, 5, 1), ISIN, d(4), "Angel One", "Groww")
    ledger.add_transfer(person, date(2025, 5, 2), ISIN, d(4), "Groww", "Upstox")
    ledger.add_transfer(person, date(2025, 5, 3), ISIN, d(1), "Zerodha", "Upstox")
    ledger.unmap_name(person, "angelone", NAME)
    assert [t.instrument for t in ledger.transfers(person)] == [KEY, KEY, ISIN]


def test_shared_undo_keeps_a_price_changed_on_the_isin_since(ledger: Ledger,
                                                             person: int) -> None:
    """Changed after the match, with another account holding the ISIN: it may be about either
    holding, so it stays on the ISIN, and the name gets its own price back."""
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2017-04-02", "BUY", 10, 100, "1"),
        replace(buy("2017-04-03", 5, 100, instrument=ISIN, trade_id="Z:1"), account="Zerodha")])
    ledger.save_settings(person, Settings(fmv_2018={KEY: d(50)}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.save_settings(person, replace(ledger.settings(person), fmv_2018={ISIN: d(55)}))
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.settings(person).fmv_2018 == {KEY: d(50), ISIN: d(55)}


def test_three_names_on_one_isin_hand_the_price_on(ledger: Ledger, person: int) -> None:
    """A's price is on the ISIN; B (with its own price) and C (none) are matched after. Undoing
    A hands the ISIN B's price, as if B had been matched first; undoing the rest leaves the
    ISIN with nothing and each name with its own."""
    names = {"A": d(50), "B": d(60), "C": None}
    ledger.import_trades(person, Batch(file_sha256="a"), [
        replace(angel("2017-04-02", "BUY", 1, 1, str(n)), instrument=f"NAME:{x}")
        for n, x in enumerate(names)])
    ledger.save_settings(person, Settings(fmv_2018={f"NAME:{x}": v for x, v in names.items()
                                                    if v is not None}))
    for x in names:
        ledger.map_name(person, "angelone", x, ISIN)
    ledger.unmap_name(person, "angelone", "A")
    assert ledger.settings(person).fmv_2018 == {"NAME:A": d(50), ISIN: d(60)}
    ledger.unmap_name(person, "angelone", "C")
    ledger.unmap_name(person, "angelone", "B")
    settings = ledger.settings(person)
    assert settings.fmv_2018 == {"NAME:A": d(50), "NAME:B": d(60)}
    assert settings.names == {}
