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
        upstox_row("2025-06-03", "BUY", 75, "24000", symbol="NIFTY25JUNFUT", isin="",
                   exchange="NFO", segment="FO", trade_id="12"),
        upstox_row("2025-06-20", "SELL", 75, "24100", symbol="NIFTY25JUNFUT", isin="",
                   exchange="NFO", segment="FO", trade_id="13"),
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
    header = ["Date", "Exchange", "Symbol", "ISIN", "Side", "Quantity", "Price", "Trade Num"]
    text = write_csv(header, [
        ["2025-05-02", "NSE", "SYNTHA", "INE000A01011", "Buy", "5", "10", "1"],
        ["2025-05-02", "NFO", "NIFTY25JUNFUT", "", "Sell", "75", "24000", "2"],
        ["2025-05-02", "MCX", "GOLD", "", "Buy", "1", "1", "3"],
    ], preamble=[["Trade Report"], ["Client: SYNTHETIC"], []])
    result = parse_upstox_tradebook(text, name="upstox.csv")
    assert [t.segment for t in result.trades] == [Segment.EQUITY, Segment.FNO]
    assert any("upstox.csv row 7: segment 'MCX' not supported" in w for w in result.warnings)


def test_upstox_other_segments_skipped_and_files_merged() -> None:
    a = write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1", trade_id="1"),
                                  upstox_row("2025-05-02", "BUY", 1, "83", segment="CD",
                                             exchange="CDS", isin="", trade_id="2")])
    result = parse_upstox_tradebooks([("a.csv", a), ("b.csv", a)])
    assert len(result.trades) == 1
    assert any("'CD' not supported" in w for w in result.warnings)
    assert any("more than one file" in w for w in result.warnings)


def test_wrong_file_names_missing_columns() -> None:
    with pytest.raises(ImportFormatError, match=r"missing column.*trade_id"):
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
