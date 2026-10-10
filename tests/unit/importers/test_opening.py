"""Opening holdings (brief 0001 task 7). Synthetic data and fake (check-digit valid) ISINs."""

from datetime import date

import pytest

from engine.api import compute_tax_year
from engine.models import Side
from importers.base import ImportFormatError
from importers.opening import load_opening_csv, parse_rows, template_csv
from tests.golden.helpers import d, sell

ISIN = "INE000A01012"
TODAY = date(2026, 10, 10)


def csv_bytes(*rows: str, header: str = "isin,name,quantity,buy_date,price,charges,how_acquired"
              ) -> bytes:
    return "\n".join([header, *rows]).encode()


def test_template_reads_back_as_one_lot() -> None:
    opening = load_opening_csv(template_csv().encode(), name="t.csv", today=TODAY)
    [lot] = opening.trades
    assert (lot.instrument, lot.quantity, lot.price, lot.charges, lot.side) == (
        ISIN, d(100), d("245.50"), d("12.40"), Side.BUY)
    assert lot.trade_id.startswith("OPENING:")
    assert opening.how_acquired[lot.trade_id] == "bought"
    assert opening.names == {ISIN: "EXAMPLE LTD (replace this row)"}


def test_rows_with_defaults_bonus_and_blank_lines() -> None:
    opening = load_opening_csv(csv_bytes(
        f"{ISIN},,10,13/04/2016,100,,", "", f"{ISIN.lower()},,5,2017-06-01,0,,BONUS",
        header="ISIN,Name,Quantity,Buy_Date,Price,Charges,How_Acquired"), name="h.csv",
        today=TODAY)
    first, bonus = opening.trades
    assert (first.trade_date, first.charges) == (date(2016, 4, 13), d(0))
    assert opening.how_acquired[first.trade_id] == "bought"
    assert (bonus.price, opening.how_acquired[bonus.trade_id]) == (d(0), "bonus")
    assert opening.names == {}


@pytest.mark.parametrize(("row", "message"), [
    (f"{ISIN},,,2016-04-01,100,,", "missing quantity"),
    ("INE000A01011,,10,2016-04-01,100,,", "isn't a valid Indian ISIN"),
    (f"{ISIN},,10,2027-01-01,100,,", "in the future"),
    (f"{ISIN},,10,2016-04-01,0,,ipo", "price 0 is only for bonus"),
    (f"{ISIN},,10,2016-04-01,100,,lottery", "how_acquired must be one of"),
    (f"{ISIN},,ten,2016-04-01,100,,", "not a number"),
    (f"{ISIN},,0,2016-04-01,100,,", "quantity must be positive"),
    (f"{ISIN},,10,4/1/2016,100,,", "could be day/month or month/day"),
    (f"{ISIN},,10,04-01-2016,100,,", "could be day/month or month/day"),
])
def test_bad_rows_are_refused_with_the_row(row: str, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message) as caught:
        load_opening_csv(csv_bytes(row), name="o.csv", today=TODAY)
    assert "o.csv row 1" in str(caught.value)


def test_a_file_that_isnt_the_template_or_is_empty() -> None:
    with pytest.raises(ImportFormatError, match="not Kosh's opening-holdings template"):
        load_opening_csv(b"symbol,qty\nX,1\n", name="x.csv", today=TODAY)
    with pytest.raises(ImportFormatError, match="no holdings to add"):
        load_opening_csv(csv_bytes(), name="e.csv", today=TODAY)
    with pytest.raises(ImportFormatError, match="no holdings to add"):
        parse_rows([{"isin": " ", "name": ""}], today=TODAY)  # a blank form row


def test_an_opening_lot_is_matched_first_and_never_intraday() -> None:
    """100 held since 2016 @100, 100 more bought and 100 sold on 2-Jun-2025 @300: FIFO sells
    the 2016 lot (LTCG 20,000), and the same-day buy isn't netted as intraday against it."""
    opening = parse_rows([{"isin": ISIN, "quantity": "100", "buy_date": "2025-06-02",
                           "price": "100"}], today=TODAY)
    report = compute_tax_year(2025, [*opening.trades,
                                     sell("2025-06-02", 100, 300, instrument=ISIN)])
    assert report.business.lines == ()
    assert [line.gain for line in report.capital_gains] == [d(20000)]
    assert any(n.code == "OPENING_HOLDINGS" and n.question == "Q-029" for n in report.warnings)


def test_ids_are_unique_and_identical_lots_in_one_save_stay_two() -> None:
    opening = parse_rows([
        {"isin": ISIN, "quantity": "10", "buy_date": "2016-04-01", "price": "100"},
        {"isin": ISIN, "quantity": "5", "buy_date": "2016-04-01", "price": "100"},
        {"isin": ISIN, "quantity": "10", "buy_date": "2016-04-01", "price": "100.00"},
    ], today=TODAY)
    ids = [t.trade_id for t in opening.trades]
    assert len(set(ids)) == 3
    assert ids[0].endswith(":1") and ids[2].endswith(":2")  # same lot twice in one save


def test_encoding_and_fractional_quantities_are_reported() -> None:
    data = csv_bytes(f"{ISIN},CAFX,1.5,2016-04-01,100,,").replace(b"CAFX", b"CAF\xc9")
    opening = load_opening_csv(data, name="w.csv", today=TODAY)
    assert any("not UTF-8" in w for w in opening.warnings)
    assert any("fractional share quantity 1.5" in w for w in opening.warnings)


def test_account_and_entry_date_columns() -> None:
    opening = parse_rows([
        {"isin": ISIN, "quantity": "10", "buy_date": "2016-04-01", "price": "100",
         "how_acquired": "transfer", "account": "  Groww  ", "entered_on": "2020-01-02"},
        {"isin": ISIN, "quantity": "5", "buy_date": "2017-04-03", "price": "90",
         "how_acquired": "transfer"},
    ], today=TODAY)
    moved, undated = opening.trades
    assert (moved.account, moved.entered_on) == ("Groww", date(2020, 1, 2))
    assert (undated.account, undated.entered_on) == (None, None)
    assert any("no entered_on date" in w for w in opening.warnings)


@pytest.mark.parametrize(("entered", "message"), [("2015-01-01", "between the purchase date"),
                                                   ("2027-01-01", "between the purchase date"),
                                                   ("04-01-2020", "could be day/month")])
def test_bad_entry_dates(entered: str, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        parse_rows([{"isin": ISIN, "quantity": "1", "buy_date": "2016-04-01", "price": "1",
                     "entered_on": entered}], today=TODAY)
