"""QA review of brief 0007 (names to ISINs): data-integrity edge cases. Synthetic data only.

Each test was a bug found in review (strict xfail until fixed)."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from engine.classify.funds import FundClass
from engine.ledger import Batch, Ledger
from engine.ledger.settings import ManualBuy, Settings
from engine.models import Trade
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


def angel(on: str, side: str, n: int, price: int, number: str, instrument: str = KEY) -> Trade:
    make = buy if side == "BUY" else sell
    return replace(make(on, n, price, instrument=instrument,
                        trade_id=f"ANGELONE:NSE:{on}:{number}"), account="Angel One")


def test_manual_buy_under_a_name_moves_with_it(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-06-02", "SELL", 4, 120, "2")])
    manual = ManualBuy(buy("2020-01-01", 4, 50, instrument=KEY, trade_id="MANUAL:1"), "bought",
                       "ANGELONE:NSE:2025-06-02:2")
    ledger.save_settings(person, Settings(manual_buys=(manual,)))
    ledger.map_name(person, "angelone", NAME, ISIN)
    assert ledger.settings(person).manual_buys[0].trade.instrument == ISIN


def test_undo_returns_a_manual_buy_after_a_settings_save(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-06-02", "SELL", 4, 120, "2")])
    manual = ManualBuy(buy("2020-01-01", 4, 50, instrument=KEY, trade_id="MANUAL:1"), "bought",
                       "ANGELONE:NSE:2025-06-02:2")
    ledger.save_settings(person, Settings(manual_buys=(manual,)))
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.save_settings(person, ledger.settings(person))  # what the UI does on the reply
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.settings(person).manual_buys[0].trade.instrument == KEY


def test_undo_after_a_later_import_keeps_the_company_together(ledger: Ledger,
                                                              person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-04-02", "BUY", 10, 100, "1")])
    ledger.map_name(person, "angelone", NAME, ISIN)
    later = angel("2025-06-02", "SELL", 10, 120, "2", instrument=ISIN)  # the alias applied
    ledger.import_trades(person, Batch(file_sha256="b"), [later],
                         raw_names={later.trade_id: NAME})  # as the Angel One import passes it
    ledger.unmap_name(person, "angelone", NAME)
    assert {t.instrument for t in ledger.trades(person)} == {KEY}


def test_a_remembered_name_is_listed_after_a_reimport(ledger: Ledger, person: int) -> None:
    ledger.map_name(person, "angelone", NAME, ISIN)  # e.g. after undoing the first import
    ledger.import_trades(person, Batch(file_sha256="b"),
                         [angel("2025-04-02", "BUY", 10, 100, "1", instrument=ISIN)])
    assert ledger.mapped_names(person) == [(NAME, ISIN)]


def test_undo_returns_a_moved_transfer_when_the_isin_is_shared(ledger: Ledger,
                                                               person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2025-04-02", "BUY", 10, 100, "1"),
        replace(buy("2025-04-03", 5, 100, instrument=ISIN, trade_id="Z:1"), account="Zerodha")])
    ledger.add_transfer(person, date(2025, 5, 1), KEY, d(1), "Angel One", "Zerodha")
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.transfers(person)[0].instrument == KEY


def test_undo_keeps_the_names_31_jan_2018_price(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2017-04-02", "BUY", 10, 100, "1")])
    ledger.save_settings(person, Settings(fmv_2018={KEY: d(50)}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.settings(person).fmv_2018.get(KEY) == d(50)


def test_a_guessed_class_stays_guessed_when_merged(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-04-02", "BUY", 10, 100, "1")])
    ledger.save_settings(person, Settings(fund_classes={KEY: FundClass.EQUITY_ORIENTED},
                                          guessed=frozenset({KEY}), names={ISIN: "Example"}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    assert ISIN in ledger.settings(person).guessed


def test_a_confirmed_name_is_only_that_persons(ledger: Ledger, person: int) -> None:
    """Names are confirmed per person: another person's imports and Undo are unaffected."""
    other = ledger.ensure_profile("Synthetic two").id
    ledger.map_name(person, "angelone", NAME, ISIN)
    assert ledger.aliases(person, "angelone") == {NAME: ISIN}
    assert ledger.aliases(other, "angelone") == {} and ledger.mapped_names(other) == []


# -- second review (fixes from the first) -------------------------------------------------------

def zerodha(on: str, n: int, number: str) -> Trade:
    return replace(buy(on, n, 100, instrument=ISIN, trade_id=f"Z:{number}"), account="Zerodha")


def test_undo_returns_a_purchase_entered_after_the_match(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2025-06-02", "SELL", 4, 120, "2")],
                         raw_names={"ANGELONE:NSE:2025-06-02:2": NAME})
    ledger.map_name(person, "angelone", NAME, ISIN)
    manual = ManualBuy(buy("2020-01-01", 4, 50, instrument=ISIN, trade_id="MANUAL:1"), "bought",
                       "ANGELONE:NSE:2025-06-02:2")
    ledger.save_settings(person, replace(ledger.settings(person), manual_buys=(manual,)))
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.settings(person).manual_buys[0].trade.instrument == KEY


def test_shared_undo_takes_the_names_price_off_the_isin(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"),
                         [angel("2017-04-02", "BUY", 10, 100, "1"), zerodha("2017-04-03", 5, "1")])
    ledger.save_settings(person, Settings(fmv_2018={KEY: d(50)}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.unmap_name(person, "angelone", NAME)
    settings = ledger.settings(person)
    assert settings.fmv_2018.get(KEY) == d(50)
    assert ISIN not in settings.fmv_2018 and ISIN not in settings.names


def test_shared_undo_returns_a_later_transfer_of_the_names_shares(ledger: Ledger,
                                                                  person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"),
                         [angel("2025-04-02", "BUY", 10, 100, "1"), zerodha("2025-04-03", 5, "1")],
                         raw_names={"ANGELONE:NSE:2025-04-02:1": NAME})
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.add_transfer(person, date(2025, 5, 1), ISIN, d(1), "Angel One", "Zerodha")
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.transfers(person)[0].instrument == KEY


def test_two_names_undone_in_either_order_restore_everything(ledger: Ledger,
                                                              person: int) -> None:
    other = "EXAMPLE DEPOSITORY"
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2017-04-02", "BUY", 10, 100, "1"),
        replace(angel("2017-04-03", "BUY", 1, 1, "3"), instrument=f"NAME:{other}")])
    ledger.save_settings(person, Settings(fmv_2018={KEY: d(50), f"NAME:{other}": d(60)}))
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.map_name(person, "angelone", other, ISIN)
    ledger.unmap_name(person, "angelone", NAME)
    ledger.unmap_name(person, "angelone", other)
    settings = ledger.settings(person)
    assert settings.fmv_2018 == {KEY: d(50), f"NAME:{other}": d(60)}
    assert {t.instrument for t in ledger.trades(person)} == {KEY, f"NAME:{other}"}


# -- third review ----------------------------------------------------------------------------

def test_match_undo_twice_gives_the_same_ledger(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"),
                         [angel("2017-04-02", "BUY", 10, 100, "1"), zerodha("2017-04-03", 5, "1")])
    ledger.save_settings(person, Settings(fmv_2018={KEY: d(50), ISIN: d(55)},
                                          fund_classes={KEY: FundClass.EQUITY_ORIENTED},
                                          guessed=frozenset({KEY})))
    ledger.add_transfer(person, date(2017, 5, 1), KEY, d(1), "Angel One", "Zerodha")
    first = (ledger.trades(person), ledger.transfers(person), ledger.settings(person))
    for _ in range(2):
        ledger.map_name(person, "angelone", NAME, ISIN)
        ledger.save_settings(person, ledger.settings(person))  # the UI's save after the reply
        ledger.unmap_name(person, "angelone", NAME)
        assert (ledger.trades(person), ledger.transfers(person), ledger.settings(person)) == first


def test_unshared_undo_takes_a_price_changed_since_to_the_name(ledger: Ledger,
                                                               person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [angel("2017-04-02", "BUY", 10, 100, "1")])
    ledger.save_settings(person, Settings(fmv_2018={ISIN: d(55)}))  # the ISIN's own, before
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.save_settings(person, replace(ledger.settings(person), fmv_2018={ISIN: d(70)}))
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.settings(person).fmv_2018 == {KEY: d(70), ISIN: d(55)}


def test_a_transfer_from_an_account_holding_two_names_stays_on_the_isin(ledger: Ledger,
                                                                         person: int) -> None:
    other = "EXAMPLE DEPOSITORY"
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2025-04-02", "BUY", 10, 100, "1"),
        replace(angel("2025-04-03", "BUY", 1, 1, "3"), instrument=f"NAME:{other}")])
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.map_name(person, "angelone", other, ISIN)
    ledger.add_transfer(person, date(2025, 5, 1), ISIN, d(1), "Angel One", "Zerodha")
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.transfers(person)[0].instrument == ISIN  # could be the other name's shares
    assert {t.instrument for t in ledger.trades(person)} == {KEY, ISIN}


def test_a_purchase_for_a_zerodha_sale_on_the_isin_stays_there(ledger: Ledger,
                                                               person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [
        angel("2025-04-02", "BUY", 10, 100, "1"),
        replace(sell("2025-06-02", 4, 120, instrument=ISIN, trade_id="Z:9"), account="Zerodha")],
        raw_names={"ANGELONE:NSE:2025-04-02:1": NAME})
    ledger.map_name(person, "angelone", NAME, ISIN)
    manual = ManualBuy(replace(buy("2020-01-01", 4, 50, instrument=ISIN, trade_id="MANUAL:1"),
                               account="Zerodha"), "bought", "Z:9")
    ledger.save_settings(person, replace(ledger.settings(person), manual_buys=(manual,)))
    ledger.unmap_name(person, "angelone", NAME)
    assert ledger.settings(person).manual_buys[0].trade.instrument == ISIN


OTHER = "EXAMPLE DEPOSITORY"


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("undo_first", [NAME, OTHER])
@pytest.mark.parametrize("prices", [(None, None, None), (50, None, None), (None, 60, None),
                                    (50, 60, None), (None, None, 55), (50, None, 55),
                                    (50, 60, 55), (None, 60, 55)])
def test_two_names_matched_and_undone_restore_every_price(
        ledger: Ledger, person: int, prices: tuple[int | None, ...], undo_first: str,
        shared: bool) -> None:
    trades = [angel("2017-04-02", "BUY", 10, 100, "1"),
              replace(angel("2017-04-03", "BUY", 1, 1, "3"), instrument=f"NAME:{OTHER}")]
    ledger.import_trades(person, Batch(file_sha256="a"),
                         [*trades, zerodha("2017-04-04", 5, "1")] if shared else trades)
    keys = (KEY, f"NAME:{OTHER}", ISIN)
    ledger.save_settings(person, Settings(fmv_2018={k: d(p) for k, p in zip(keys, prices,
                                                                            strict=True) if p}))
    first = ledger.settings(person)
    ledger.map_name(person, "angelone", NAME, ISIN)
    ledger.map_name(person, "angelone", OTHER, ISIN)
    ledger.save_settings(person, ledger.settings(person))
    ledger.unmap_name(person, "angelone", undo_first)
    ledger.unmap_name(person, "angelone", OTHER if undo_first == NAME else NAME)
    assert ledger.settings(person) == first
