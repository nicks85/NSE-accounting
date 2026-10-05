from datetime import date
from decimal import Decimal

import pytest

from engine.api import compute_tax_year
from engine.models import Segment, Side
from importers.base import ImportFormatError, parse_date, parse_decimal
from importers.zerodha import parse_zerodha_tradebook, parse_zerodha_tradebooks
from tests.fixtures.zerodha import HEADER, Row, random_rows, tradebook_csv


def test_parses_equity_rows() -> None:
    result = parse_zerodha_tradebook(tradebook_csv([
        Row("SYNTHA", "2025-05-01", "buy", "10", "1000.50"),
        Row("SYNTHA", "2025-06-01", "sell", "10", "1100"),
    ]))
    buy, sell = result.trades
    assert (buy.instrument, buy.side, buy.quantity, buy.price) == (
        "INE000A01011", Side.BUY, Decimal(10), Decimal("1000.50"))
    assert sell.trade_date == date(2025, 6, 1) and sell.side is Side.SELL
    assert buy.trade_id == "ZERODHA:NSE:2025-05-01:00000001"
    assert not result.format_confirmed
    assert any("Q-015" in w for w in result.warnings)


def test_title_case_headers_and_bom() -> None:
    header = [h.replace("_", " ").title() for h in HEADER]
    text = "﻿" + tradebook_csv([Row("SYNTHA", "2025-05-01", "BUY", "1", "1")], header=header)
    assert len(parse_zerodha_tradebook(text).trades) == 1


def test_fno_uses_symbol_and_skips_unsupported_segments() -> None:
    result = parse_zerodha_tradebook(tradebook_csv([
        Row("NIFTY25JUNFUT", "2025-06-02", "sell", "75", "24000", segment="FO", exchange="NFO"),
        Row("USDINR25JUNFUT", "2025-06-02", "buy", "1", "83", segment="CDS", exchange="CDS"),
    ]))
    [fut] = result.trades
    assert (fut.instrument, fut.segment) == ("NIFTY25JUNFUT", Segment.FNO)
    assert any("'CDS' not supported" in w for w in result.warnings)


def test_same_day_rows_sorted_by_execution_time() -> None:
    result = parse_zerodha_tradebook(tradebook_csv([
        Row("SYNTHA", "2025-06-02", "buy", "1", "10", time="14:00:00"),
        Row("SYNTHA", "2025-06-02", "sell", "1", "11", time="10:00:00"),
    ]))
    assert [t.side for t in result.trades] == [Side.SELL, Side.BUY]


def test_duplicates_and_blank_lines() -> None:
    text = tradebook_csv([Row("SYNTHA", "2025-05-01", "buy", "1", "1")])
    lines = text.splitlines()
    text = "\n".join([*lines, lines[1], "", ","]) + "\n"
    result = parse_zerodha_tradebook(text)
    assert len(result.trades) == 1
    assert any("duplicate trade" in w for w in result.warnings)


@pytest.mark.parametrize(("bad", "message"), [
    ("", "file is empty"),
    ("symbol,isin\nX,Y\n", "missing column"),
])
def test_rejects_wrong_files(bad: str, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        parse_zerodha_tradebook(bad)


@pytest.mark.parametrize(("row", "message"), [
    (Row("SYNTHA", "2025-05-01", "hold", "1", "1"), "row 2: trade_type 'hold'"),
    (Row("SYNTHA", "2025-05-01", "buy", "ten", "1"), "row 2 quantity: not a number"),
    (Row("SYNTHA", "05.01.2025", "buy", "1", "1"), "row 2 trade_date: unrecognised date"),
    (Row("SYNTHA", "2025-05-01", "buy", "0", "1"), "row 2: .*quantity must be positive"),
    (Row("UNKNOWN", "2025-05-01", "buy", "1", "1"), "row 2: missing isin"),
])
def test_row_errors_name_the_row(row: Row, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        parse_zerodha_tradebook(tradebook_csv([row]))


def test_parsers() -> None:
    assert parse_decimal(" 1,234.50 ", where="x") == Decimal("1234.50")
    with pytest.raises(ImportFormatError, match="finite"):
        parse_decimal("NaN", where="x")
    for text in ("2025-05-01", "01-05-2025", "01/05/2025", "2025/05/01", "2025-05-01T09:15:00",
                 "2025-05-01 09:15:00"):
        assert parse_date(text, where="x") == date(2025, 5, 1)


def test_multiple_files_deduplicated() -> None:
    a = tradebook_csv([Row("SYNTHA", "2025-05-01", "buy", "1", "1")])
    result = parse_zerodha_tradebooks([a, a])
    assert len(result.trades) == 1
    assert any("more than one file" in w for w in result.warnings)


def test_round_trip_fixture_to_gains() -> None:
    """Synthetic tradebook → trades → FY 2025-26 tax.
    SYNTHA: buy 100 @1,000, sell 100 @1,200 → STCG 20,000.
    SYNTHB: intraday buy/sell 50 @500/510 → speculative 500.
    NIFTY future: sell 75 @24,000, buy back @23,900 → F&O 7,500.
    Tax on STCG 20,000 x 20% = 4,000."""
    result = parse_zerodha_tradebook(tradebook_csv([
        Row("SYNTHA", "2025-05-01", "buy", "100", "1000"),
        Row("SYNTHB", "2025-06-02", "buy", "50", "500", time="09:20:00"),
        Row("SYNTHB", "2025-06-02", "sell", "50", "510", time="15:10:00"),
        Row("NIFTY25JUNFUT", "2025-06-03", "sell", "75", "24000", segment="FO", exchange="NFO"),
        Row("NIFTY25JUNFUT", "2025-06-20", "buy", "75", "23900", segment="FO", exchange="NFO"),
        Row("SYNTHA", "2025-10-01", "sell", "100", "1200"),
    ]))
    report = compute_tax_year(2025, result.trades)
    assert report.special_rate_tax_rounded == Decimal(4000)
    assert report.business.speculative == Decimal(500)
    assert report.business.non_speculative == Decimal(7500)


def test_round_trip_random_tradebooks_conserve_quantity() -> None:
    for seed in range(20):
        rows = random_rows(seed, 60)
        trades = parse_zerodha_tradebook(tradebook_csv(rows)).trades
        assert len(trades) == len(rows)
        report = compute_tax_year(2025, trades)
        bought = sum(t.quantity for t in trades if t.side is Side.BUY)
        sold = sum(t.quantity for t in trades if t.side is Side.SELL)
        still_open = sum(lot.quantity for lot in report.open_lots)
        intraday_squared = sum(line.disposal.quantity for line in report.business.lines)
        assert bought - sold == still_open
        assert sum(line.disposal.quantity for line in report.capital_gains) + (
            intraday_squared) == sold


def test_multiple_distinct_files_merge_in_date_order() -> None:
    later = tradebook_csv([Row("SYNTHA", "2025-06-01", "sell", "1", "2")])
    earlier = tradebook_csv([Row("SYNTHB", "2024-05-01", "buy", "1", "1")])
    result = parse_zerodha_tradebooks([later, earlier])
    assert [t.trade_date.year for t in result.trades] == [2024, 2025]
    assert not any("more than one file" in w for w in result.warnings)
