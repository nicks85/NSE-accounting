"""Synthetic Angel One trade history ("TradesAndCharges"), laid out like a real export: client
block, date range, charges summary, then the trade table. Names, ISINs, client code, order and
trade numbers are all invented. Synthetic data only (CLAUDE.md rule 4)."""

from dataclasses import dataclass
from decimal import Decimal

from tests.fixtures.tradebooks import write_csv
from tests.fixtures.xlsx import Cell, xlsx_bytes

HEADER = ["Scrip/Contract", "Buy/Sell", "Buy Price", "Sell Price", "Quantity", "Brokerage", "GST",
          "STT", "Sebi Tax", "Exchange Turnover Charges", "Stamp Duty", "Other Charges",
          "IPFT Charges", "Order Type", "Segment", "Exchange", "Order ID", "Trade ID", "Date"]


def isin_with_check_digit(body: str) -> str:
    """Complete an 11-character ISIN body with its ISO 6166 check digit."""
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char) * (1 if position % 2 else 2)
        total += value - 9 if value > 9 else value
    return body + str((10 - total % 10) % 10)


SYNTH_A = isin_with_check_digit("INE9SA00101")
SYNTH_B = isin_with_check_digit("INE9SB00101")
ISINS = {"SYNTHETIC ALPHA LTD": SYNTH_A, "SYNTH BETA IND (I)": SYNTH_B}


@dataclass(frozen=True)
class Row:
    scrip: str
    side: str
    price: str
    quantity: int | str
    date: str
    trade_id: str
    brokerage: str = "0"
    gst: str = "0"
    stt: str = "0"
    sebi: str = "0"
    exchange_charges: str = "0"
    stamp: str = "0"
    order_type: str = "Delivery"
    segment: str = "CAPITAL"
    order_id: str = "1000000000000001"

    def cells(self) -> list[str]:
        buy = self.side.upper() == "BUY"
        return [self.scrip, self.side, self.price if buy else "", "" if buy else self.price,
                str(self.quantity), self.brokerage, self.gst, self.stt, self.sebi,
                self.exchange_charges, self.stamp, "0", "0", self.order_type, self.segment, "NSE",
                self.order_id, self.trade_id, self.date]

    def charges(self) -> Decimal:
        return sum((Decimal(x) for x in (self.brokerage, self.gst, self.stt, self.sebi,
                                         self.exchange_charges, self.stamp)), Decimal(0))


def preamble(rows: list[Row], *, total_trade_charges: str | None = None,
             dp_charges: str = "0") -> list[list[str]]:
    total = total_trade_charges or str(sum((r.charges() for r in rows), Decimal(0)))
    return [
        [],
        ["ClientCode", "SYNTH01"],
        ["DateOfDownload", "2026-04-02"],
        [],
        ["Date Range"],
        ["StartDate", "EndDate"],
        ["2025-04-01 00:00:00.0", "2026-03-31 23:59:59.0"],
        [],
        ["Charges Summary"],
        ["Total Trades", str(len({r.order_id for r in rows}))],
        ["Total Charges", total],
        ["Total Trade Charges", total],
        ["Total Non Trade Charges", dp_charges],
        [],
        ["Trade Charges"],
        *([label, "0"] for label in ("Brokerage", "GST", "SEBI Tax", "STT",
                                     "Exchange Turnover Charges", "Stamp Duty", "Other Charges",
                                     "IPFT Charges")),
        [],
        ["Non Trade Charges"],
        ["DP Charges", dp_charges],
        *([label, "0"] for label in ("Interest Charges", "Monthly Account Maintenance",
                                     "Pledge Charges", "Call And Trade Charges",
                                     "Margin Shortfall Penalty")),
        [],
        [],
        ["TradeBook And Charges"],
    ]


def trades_xlsx(rows: list[Row], **summary: str) -> bytes:
    lines: list[list[Cell]] = [*preamble(rows, **summary), list(HEADER),
                               *(list(r.cells()) for r in rows)]
    return xlsx_bytes(lines, sheet_name="TradesAndCharges")


def trades_csv(rows: list[Row], **summary: str) -> bytes:
    return write_csv(HEADER, [r.cells() for r in rows],
                     preamble=preamble(rows, **summary)).encode()
