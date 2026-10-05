from decimal import Decimal

import pytest

from engine.api import compute_tax_year
from engine.models import Segment, Side
from importers.base import ImportFormatError
from importers.mapped import mapped_profile, parse_mapped_tradebook, parse_mapped_tradebooks
from importers.upstox import parse_upstox_tradebook, parse_upstox_tradebooks
from tests.fixtures.tradebooks import UPSTOX_HEADER, upstox_row, write_csv


def test_upstox_round_trip_to_tax() -> None:
    """SYNTHA: buy 100 @1,000, sell @1,150 → STCG 15,000 → 3,000.
    NIFTY future: buy 75 @24,000, sell @24,100 → F&O 7,500."""
    text = write_csv(UPSTOX_HEADER, [
        upstox_row("2025-05-02", "BUY", 100, "1000", trade_id="11"),
        upstox_row("2025-06-03", "BUY", 75, "24000", symbol="NIFTY", isin="",
                   exchange="NFO", segment="FO", trade_id="12", expiry="2025-06-26"),
        upstox_row("2025-06-20", "SELL", 75, "24100", symbol="NIFTY", isin="",
                   exchange="NFO", segment="FO", trade_id="13", expiry="2025-06-26"),
        upstox_row("2025-09-01", "SELL", 100, "1150", trade_id="14"),
    ])
    result = parse_upstox_tradebook(text)
    assert result.source.startswith("Upstox") and not result.format_confirmed
    assert result.trades[0].trade_id == "UPSTOX:NSE:2025-05-02:11"
    report = compute_tax_year(2025, result.trades)
    assert report.special_rate_tax_rounded == Decimal(3000)
    assert report.business.non_speculative == Decimal(7500)
    assert any("Q-016" in w for w in result.warnings)


def test_upstox_aliases_title_rows_and_segment_from_exchange() -> None:
    header = ["Date", "Exchange", "Symbol", "ISIN", "Side", "Quantity", "Price", "Trade Num",
              "Expiry"]
    text = write_csv(header, [
        ["2025-05-02", "NSE", "SYNTHA", "INE000A01011", "Buy", "5", "10", "1", ""],
        ["2025-05-02", "NFO", "NIFTY", "", "Sell", "75", "24000", "2", "2025-06-26"],
        ["2025-05-02", "MCX", "GOLD", "", "Buy", "1", "1", "3", ""],
    ], preamble=[["Trade Report"], ["Client: SYNTHETIC"], []])
    result = parse_upstox_tradebook(text, name="upstox.csv")
    assert [t.segment for t in result.trades] == [Segment.EQUITY, Segment.FNO]
    assert result.trades[1].instrument == "NIFTY:2025-06-26:FUT"
    assert any("upstox.csv row 7: segment 'MCX' not supported" in w for w in result.warnings)
    assert any("classified as equity or F&O from the exchange" in w for w in result.warnings)


def test_upstox_other_segments_skipped_and_files_merged() -> None:
    a = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1", trade_id="1"),
                                  upstox_row("2025-05-02", "BUY", 1, "83", segment="CD",
                                             exchange="CDS", isin="", trade_id="2")])
    result = parse_upstox_tradebooks([("a.csv", a), ("b.csv", a)])
    assert len(result.trades) == 1
    assert any("'CD' not supported" in w for w in result.warnings)
    assert any("more than one file" in w for w in result.warnings)


def test_wrong_file_names_missing_columns() -> None:
    with pytest.raises(ImportFormatError, match=r"no header row with the column\(s\) trade_date"):
        parse_upstox_tradebook(write_csv(["Date", "Side"], [["2025-05-02", "BUY"]]))
    with pytest.raises(ImportFormatError, match="file is empty"):
        parse_upstox_tradebook("\n\n")


MAPPING = {"trade_date": "Deal Date", "side": "B/S", "quantity": "Qty", "price": "Rate",
           "trade_id": "Ref No", "isin": "ISIN Code", "symbol": "Contract",
           "exchange": "Exch", "executed_at": "Time"}
MAPPED_HEADER = ["Deal Date", "Exch", "Contract", "ISIN Code", "B/S", "Qty", "Rate", "Ref No",
                 "Time"]


def test_mapped_import_with_user_headers() -> None:
    profile = mapped_profile(MAPPING, source="Groww order history (mapped)", key="GROWW")
    text = write_csv(MAPPED_HEADER, [
        ["01/05/2025", "NSE", "", "INE000A01011", "B", "10", "100", "A1", "10:00"],
        ["01/05/2025", "NSE", "", "INE000A01011", "S", "10", "101", "A2", "15:00"],
        ["02/05/2025", "NFO", "NIFTY25JUNFUT", "", "S", "75", "24000", "A3", "11:00"],
    ])
    result = parse_mapped_tradebook(text, profile)
    assert [t.side for t in result.trades] == [Side.BUY, Side.SELL, Side.SELL]
    assert result.trades[0].trade_id == "GROWW:NSE:2025-05-01:A1"
    assert result.trades[2].segment is Segment.FNO
    assert any("Q-017" in w for w in result.warnings)
    report = compute_tax_year(2025, result.trades)
    assert report.business.speculative == Decimal(10)
    merged = parse_mapped_tradebooks([("x", text)], profile)
    assert len(merged.trades) == 3


def test_mapped_custom_codes() -> None:
    profile = mapped_profile(
        {"trade_date": "d", "side": "s", "quantity": "q", "price": "p", "trade_id": "id",
         "isin": "isin", "segment": "seg"},
        segment_codes={"cash": Segment.EQUITY}, side_codes={"purchase": Side.BUY})
    text = write_csv(["d", "s", "q", "p", "id", "isin", "seg"],
                     [["2025-05-01", "Purchase", "1", "1", "1", "INE000A01011", "Cash"]])
    [trade] = parse_mapped_tradebook(text, profile).trades
    assert (trade.side, trade.segment) == (Side.BUY, Segment.EQUITY)


@pytest.mark.parametrize(("mapping", "message"), [
    ({**MAPPING, "colour": "C"}, "unknown field"),
    ({"trade_date": "d"}, "mapping needs: side, quantity, price, trade_id"),
    ({**MAPPING, "isin": "", "symbol": ""}, "isin or symbol"),
    ({**MAPPING, "exchange": ""}, "segment or exchange"),
])
def test_mapping_validation(mapping: dict[str, str], message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        mapped_profile(mapping)



def _fno(day: str, side: str, trade_id: str, *, strike: str, option: str) -> list[str]:
    return upstox_row(day, side, 75, "100", symbol="BANKNIFTY", isin="", exchange="NFO",
                      segment="FO", trade_id=trade_id, expiry="2025-06-26", strike=strike,
                      option=option)


def test_upstox_option_contracts_are_distinct() -> None:
    """Same underlying, different strikes/types must not be matched against each other."""
    result = parse_upstox_tradebook(write_csv(UPSTOX_HEADER, [
        _fno("2025-06-02", "BUY", "1", strike="50000", option="CE"),
        _fno("2025-06-02", "SELL", "2", strike="50000.00", option="PE"),
        _fno("2025-06-03", "SELL", "3", strike="51000", option="CE"),
    ]))
    assert [t.instrument for t in result.trades] == [
        "BANKNIFTY:2025-06-26:50000:CE", "BANKNIFTY:2025-06-26:50000:PE",
        "BANKNIFTY:2025-06-26:51000:CE"]
    with pytest.raises(ImportFormatError, match="missing expiry"):
        parse_upstox_tradebook(write_csv(UPSTOX_HEADER, [
            upstox_row("2025-06-02", "BUY", 1, "1", symbol="NIFTY", isin="", exchange="NFO",
                       segment="FO")]))
    with pytest.raises(ImportFormatError, match="unrecognised option type 'XX'"):
        parse_upstox_tradebook(write_csv(UPSTOX_HEADER, [
            _fno("2025-06-02", "BUY", "1", strike="1", option="XX")]))


def test_alias_priority_first_alias_wins() -> None:
    header = ["trade_date", "date", "exchange", "segment", "symbol", "isin", "transaction_type",
              "side", "quantity", "price", "trade_id"]
    text = write_csv(header, [["2025-05-02", "1999-01-01", "NSE", "EQ", "SYNTHA",
                               "INE000A01011", "BUY", "SELL", "1", "1", "1"]])
    [trade] = parse_upstox_tradebook(text).trades
    assert (trade.trade_date.year, trade.side) == (2025, Side.BUY)


def test_duplicate_header_names_rejected() -> None:
    header = [*UPSTOX_HEADER, "Trade Date"]
    with pytest.raises(ImportFormatError, match="trade_date appear more than once"):
        parse_upstox_tradebook(write_csv(header, [[*upstox_row("2025-05-02", "BUY", 1, "1"),
                                                   "2025-05-03"]]))


def test_blank_segment_cell_is_skipped_not_guessed() -> None:
    text = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1", segment=""),
                                     upstox_row("2025-05-02", "BUY", 1, "1", trade_id="2")])
    result = parse_upstox_tradebook(text)
    assert len(result.trades) == 1
    assert any("segment '' not supported" in w for w in result.warnings)


def test_all_rows_skipped_is_an_error() -> None:
    text = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1", segment="MF")])
    with pytest.raises(ImportFormatError, match="none of the 1 row"):
        parse_upstox_tradebook(text)


def test_header_beyond_search_limit() -> None:
    text = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1")],
                     preamble=[["note"]] * 31)
    with pytest.raises(ImportFormatError, match="first 30 rows"):
        parse_upstox_tradebook(text)


def test_short_row_missing_only_optional_cells_is_accepted() -> None:
    row = upstox_row("2025-05-02", "BUY", 1, "1")[:11]
    assert len(parse_upstox_tradebook(write_csv(UPSTOX_HEADER, [row])).trades) == 1


def test_mapping_rejects_empty_and_shared_headers_and_strips_codes() -> None:
    with pytest.raises(ImportFormatError, match="no letters or digits"):
        mapped_profile({**MAPPING, "symbol": " # "})
    with pytest.raises(ImportFormatError, match="mapped to the same column"):
        mapped_profile({**MAPPING, "symbol": "ISIN code"})
    profile = mapped_profile({**MAPPING, "executed_at": "Deal Date"},
                             side_codes={" buy ": Side.BUY, "s": Side.SELL})
    assert profile.sides["BUY"] is Side.BUY


def test_header_with_blank_column() -> None:
    header = [*UPSTOX_HEADER[:3], "", *UPSTOX_HEADER[3:]]
    row = upstox_row("2025-05-02", "BUY", 1, "1")
    assert len(parse_upstox_tradebook(write_csv(header, [[*row[:3], "", *row[3:]]])).trades) == 1
