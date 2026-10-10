from decimal import Decimal

import pytest

from engine.api import compute_tax_year
from engine.models import Segment, Side
from importers.angel_one import NAME_PREFIX, load_angel_one_tradebooks, scrip_key
from importers.base import ImportFormatError, is_valid_isin
from tests.fixtures.angel_one import (
    HEADER,
    ISINS,
    SYNTH_A,
    SYNTH_B,
    Row,
    trades_csv,
    trades_xlsx,
)

ALPHA = "SYNTHETIC ALPHA LTD"
BETA = "SYNTH BETA IND (I)"


def _buy(trade_id: str = "11", **kw: str) -> Row:
    return Row(ALPHA, "Buy", "1000", 100, "2025-05-02", trade_id, brokerage="20", gst="3.6",
               stt="100", stamp="15", **kw)  # type: ignore[arg-type]


def _sell(trade_id: str = "12", **kw: str) -> Row:
    return Row(ALPHA, "Sell", "1200", 100, "2025-09-01", trade_id, brokerage="20", gst="3.6",
               stt="120", order_id="1000000000000002", **kw)  # type: ignore[arg-type]


def test_fixture_isins_are_valid() -> None:
    assert is_valid_isin(SYNTH_A) and is_valid_isin(SYNTH_B)
    assert is_valid_isin("INE002A01018")  # a real, public ISIN
    assert not is_valid_isin("INE002A01019")  # one-digit typo
    assert not is_valid_isin("US0378331005")  # valid ISIN, but not Indian
    assert not is_valid_isin("ine002a01018")


def test_round_trip_to_tax_with_charges() -> None:
    """Buy 100 @1,000 (charges 38.60, STT 100); sell @1,200 (charges 23.60, STT 120).
    STCG = 1,20,000 - 23.60 - (1,00,000 + 38.60) = 19,937.80; 20% = 3,987.56 → ₹3,990.
    STT is not deducted (s.48 / s.72(3)(b))."""
    imported = load_angel_one_tradebooks([("trades.xlsx", trades_xlsx([_buy(), _sell()]))],
                                         isin_map=ISINS)
    result = imported.result
    assert result.source == "Angel One trade history" and result.format_confirmed
    buy, sell = result.trades
    assert (buy.side, buy.price, buy.charges, buy.stt) == (
        Side.BUY, Decimal(1000), Decimal("38.6"), Decimal(100))
    assert (sell.side, sell.price, sell.charges, sell.stt) == (
        Side.SELL, Decimal(1200), Decimal("23.6"), Decimal(120))
    assert buy.trade_id == "ANGELONE:NSE:2025-05-02:11"
    assert sum((a for _, a in buy.charge_parts), Decimal(0)) == buy.charges  # brief 0005
    assert {k for k, _ in buy.charge_parts} <= {"BROKERAGE", "GST", "SEBI", "EXCHANGE", "STAMP",
                                                 "OTHER", "IPFT"}
    assert buy.instrument == SYNTH_A and buy.segment is Segment.EQUITY
    assert imported.names == {SYNTH_A: ALPHA}
    report = compute_tax_year(2025, result.trades)
    assert report.special_rate_tax_rounded == Decimal(3990)
    assert not any("Total Trade Charges" in w for w in result.warnings)
    assert any("no trade times" in w for w in result.warnings)


def test_unknown_scrips_import_by_name_and_known_ones_by_isin() -> None:
    """No ISIN is asked for: unknown names become NAME: keys; the gain is the same."""
    a = trades_xlsx([_buy(), Row(BETA, "Buy", "50", 10, "2025-05-03", "13")])
    b = trades_csv([Row("Synth  gamma etf", "Buy", "5", 1, "2025-05-04", "14"), _sell()])
    imported = load_angel_one_tradebooks([("a.xlsx", a), ("b.csv", b)])
    result = imported.result
    assert len(result.trades) == 4
    instruments = {t.instrument for t in result.trades}
    assert instruments == {NAME_PREFIX + ALPHA, NAME_PREFIX + BETA, NAME_PREFIX + "SYNTH GAMMA ETF"}
    assert imported.names[NAME_PREFIX + BETA] == BETA
    assert any("3 companies imported by name" in w for w in result.warnings)
    assert [w for w in result.warnings if "looks like an ETF" in w] == [
        "SYNTH GAMMA ETF looks like an ETF or fund; without its ISIN Kosh taxes it as a listed "
        "share, which is wrong for gold, debt or international ETFs (Q-024)"]
    report = compute_tax_year(2025, result.trades)
    assert report.special_rate_tax_rounded == Decimal(3990)  # as in the ISIN round trip
    # A known name uses its ISIN, matched regardless of case and spacing.
    loose = {" synthetic alpha  ltd": SYNTH_A.lower()}
    mixed = load_angel_one_tradebooks([("a.xlsx", a)], isin_map=loose)
    assert {t.instrument for t in mixed.result.trades} == {SYNTH_A, NAME_PREFIX + BETA}
    assert any("1 company imported by name" in w for w in mixed.result.warnings)
    assert scrip_key("  Synth  gamma ") == "SYNTH GAMMA"
    named = load_angel_one_tradebooks([("a.xlsx", trades_xlsx([_buy()]))], isin_map=ISINS)
    assert not any("imported by name" in w for w in named.result.warnings)


def test_wrong_sheet_name_falls_back_to_first_sheet() -> None:
    from tests.fixtures.angel_one import preamble
    from tests.fixtures.xlsx import xlsx_bytes

    rows: list[list[object]] = [*preamble([_buy()]), list(HEADER), _buy().cells()]
    data = xlsx_bytes(rows, sheet_name="Sheet1")  # type: ignore[arg-type]
    assert len(load_angel_one_tradebooks([("a.xlsx", data)]).result.trades) == 1


def test_unreadable_summary_cell_skips_the_check() -> None:
    result = load_angel_one_tradebooks(
        [("a.xlsx", trades_xlsx([_buy()], total_trade_charges="-", dp_charges="-"))]).result
    assert len(result.trades) == 1
    assert not any("Total Trade Charges" in w or "Q-027" in w for w in result.warnings)


def test_invalid_isin_is_refused() -> None:
    with pytest.raises(ImportFormatError, match="not a valid ISIN"):
        load_angel_one_tradebooks([("a.xlsx", trades_xlsx([_buy()]))],
                                  isin_map={ALPHA: "INE002A01019"})


def test_charges_checked_against_the_files_summary() -> None:
    rows = [_buy(), _sell()]  # rows total 282.20 including STT
    near = load_angel_one_tradebooks(
        [("a.xlsx", trades_xlsx(rows, total_trade_charges="282"))], isin_map=ISINS).result
    assert not any("Total Trade Charges" in w for w in near.warnings)  # rounding tolerated
    off = load_angel_one_tradebooks(
        [("a.xlsx", trades_xlsx(rows, total_trade_charges="300", dp_charges="15.93"))],
        isin_map=ISINS).result
    assert any("add up to ₹282.20" in w and "₹300.00" in w for w in off.warnings)
    assert any("dp charges ₹15.93" in w and "Q-027" in w for w in off.warnings)


def test_other_segments_and_order_types() -> None:
    rows = [_buy(), Row(ALPHA, "Buy", "1001", 5, "2025-05-05", "15", order_type="Intraday"),
            Row(ALPHA, "Sell", "1002", 5, "2025-05-05", "16", order_type="Intraday"),
            Row("NIFTY FUT", "Buy", "24000", 75, "2025-05-02", "17", segment="FO")]
    result = load_angel_one_tradebooks([("a.xlsx", trades_xlsx(rows))], isin_map=ISINS).result
    assert len(result.trades) == 3
    assert any("1 row(s) in segment 'FO' skipped" in w for w in result.warnings)
    assert any("2 row(s) with order type 'Intraday'" in w for w in result.warnings)
    report = compute_tax_year(2025, result.trades)
    assert report.business.speculative == Decimal(5)  # (1002 - 1001) x 5 intraday


def test_same_file_twice_counts_once_and_conflicts_are_refused() -> None:
    data = trades_xlsx([_buy(), _sell()])
    result = load_angel_one_tradebooks([("a.xlsx", data), ("copy.xlsx", data)],
                                       isin_map=ISINS).result
    assert len(result.trades) == 2
    assert any("more than one file" in w for w in result.warnings)
    in_file = trades_xlsx([_buy(), _buy()], total_trade_charges="138.6")
    result = load_angel_one_tradebooks([("a.xlsx", in_file)], isin_map=ISINS).result
    assert any("duplicate trade 11" in w for w in result.warnings)
    assert not any("Total Trade Charges" in w for w in result.warnings)  # repeat not counted
    clash = trades_xlsx([_buy(), Row(ALPHA, "Buy", "999", 100, "2025-05-02", "11")])
    with pytest.raises(ImportFormatError, match="appears twice with different details"):
        load_angel_one_tradebooks([("a.xlsx", clash)], isin_map=ISINS)


@pytest.mark.parametrize(("row", "message"), [
    (Row(ALPHA, "Sell", "", 1, "2025-05-02", "1"), "sell with no sell price"),
    (Row(ALPHA, "Hold", "1", 1, "2025-05-02", "1"), "is not buy or sell"),
    (Row(ALPHA, "Buy", "1", 1, "02-13-2025", "1"), "unrecognised date"),
    (Row(ALPHA, "Buy", "1", 1, "2025-05-02", ""), "missing Trade ID"),
    (Row("", "Buy", "1", 1, "2025-05-02", "1"), "missing Scrip/Contract"),
    (Row(ALPHA, "Buy", "1", 1, "2025-05-02", "1", brokerage="1,2"), "not a number"),
    (Row(ALPHA, "Buy", "-1", 1, "2025-05-02", "1"), "non-negative"),
])
def test_bad_rows_name_the_row(row: Row, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message) as caught:
        load_angel_one_tradebooks([("a.xlsx", trades_xlsx([row], total_trade_charges="0"))],
                                  isin_map=ISINS)
    assert "a.xlsx row 36" in str(caught.value)


def test_not_an_angel_one_file() -> None:
    with pytest.raises(ImportFormatError, match="no header row"):
        load_angel_one_tradebooks([("x.csv", b"a,b,c\n1,2,3\n")])
    with pytest.raises(ImportFormatError, match="file is empty"):
        load_angel_one_tradebooks([("x.csv", b"\n\n")])
    with pytest.raises(ImportFormatError, match=r"x\.xlsx"):
        load_angel_one_tradebooks([("x.xlsx", b"PK\x03\x04broken")])


def test_empty_period_and_fractional_quantity() -> None:
    empty = load_angel_one_tradebooks([("a.xlsx", trades_xlsx([]))]).result
    assert empty.trades == () and empty.warnings == ()
    data = trades_csv([Row(ALPHA, "Buy", "10", "1.5", "2025-05-02", "1")])
    result = load_angel_one_tradebooks([("a.csv", data)], isin_map=ISINS).result
    assert any("fractional share quantity 1.5" in w for w in result.warnings)


def test_plain_table_without_summary_and_blank_rows() -> None:
    """A CSV with just the header and rows (no summary block) still imports; blank lines
    between rows are ignored."""
    lines = [",".join(HEADER), ",".join(_buy().cells()), ",,,", ",".join(_sell().cells())]
    result = load_angel_one_tradebooks([("plain.csv", "\n".join(lines).encode())],
                                       isin_map=ISINS).result
    assert len(result.trades) == 2
    assert not any("Total Trade Charges" in w for w in result.warnings)


def test_unreadable_csv() -> None:
    data = (",".join(HEADER) + "\n" + "x" * 200_000 + "\n").encode()
    with pytest.raises(ImportFormatError, match="not a readable CSV"):
        load_angel_one_tradebooks([("big.csv", data)])


@pytest.mark.parametrize(("name", "flagged"), [
    ("SYNTHGOLDBEES", True), ("SYNTHNIFTYBEES", True), ("SETFSYN50", True), ("SYN MON100", True),
    ("SYNTH LIQUIDFUND", True), ("SYNTHETIC ALPHA LTD", False),
])
def test_fund_like_names_are_flagged_anywhere_in_the_name(name: str, flagged: bool) -> None:
    data = trades_xlsx([Row(name, "Buy", "10", 1, "2025-05-02", "1")])
    warnings = load_angel_one_tradebooks([("a.xlsx", data)]).result.warnings
    assert any("looks like an ETF" in w for w in warnings) is flagged
