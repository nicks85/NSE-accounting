"""Zerodha Console tradebook (CSV) importer.

FORMAT UNCONFIRMED (docs/OPEN_QUESTIONS.md Q-015). Zerodha's support docs confirm the
tradebook is downloadable from Console → Reports → Tradebook as CSV or XLSX, for at most
365 days per download, and that "external trades" (corporate actions, IPO, OFS, buyback,
transfers between brokers) are not in it:
https://support.zerodha.com/category/console/reports/articles/where-can-i-see-all-the-trades-i-ve-taken-for-a-particular-period
The column names below come from secondary sources (third-party parsers), not from Zerodha:
symbol, isin, trade_date, exchange, segment, series, trade_type, auction, quantity, price,
trade_id, order_id, order_execution_time. Headers are matched by name, ignoring case,
spaces and punctuation, so "Trade Date" and "trade_date" both work.

The tradebook has no brokerage, other charges or STT, so trades are imported with zero charges.
That overstates gains slightly (conservative) until charges are imported from the contract
notes or Tax P&L.
"""

from collections.abc import Iterable

from engine.models import Segment, Side
from importers.base import ImportResult
from importers.tabular import (
    BrokerProfile,
    load_tradebook,
    load_tradebooks,
    parse_tradebook,
    parse_tradebooks,
)

NOTES = (
    "Zerodha tradebook format is unconfirmed (docs/OPEN_QUESTIONS.md Q-015); check the "
    "imported trades against Console.",
    "The tradebook has no brokerage, charges or STT; they were set to zero, which slightly "
    "overstates gains. Import charges from contract notes or the Tax P&L when available.",
    "The tradebook excludes corporate actions, IPO/OFS allotments, buybacks and transfers "
    "from other brokers; add those separately or FIFO matching may fail.",
)

PROFILE = BrokerProfile(
    key="ZERODHA",
    source="Zerodha tradebook (CSV)",
    columns={
        "symbol": ("symbol",), "isin": ("isin",), "trade_date": ("trade_date",),
        "exchange": ("exchange",), "segment": ("segment",), "side": ("trade_type",),
        "quantity": ("quantity",), "price": ("price",), "trade_id": ("trade_id",),
        "executed_at": ("order_execution_time",), "auction": ("auction",),
    },
    required=("symbol", "isin", "trade_date", "exchange", "segment", "side", "quantity",
              "price", "trade_id"),
    segments={"EQ": Segment.EQUITY, "FO": Segment.FNO},
    sides={"BUY": Side.BUY, "SELL": Side.SELL},
    confirmed=False,
    notes=NOTES,
    question="Q-015",
)


def parse_zerodha_tradebook(text: str, *, name: str = "tradebook") -> ImportResult:
    """Parse the text of a Zerodha tradebook CSV export. ``name`` labels warnings."""
    return parse_tradebook(text, PROFILE, name=name)


def parse_zerodha_tradebooks(files: Iterable[tuple[str, str]]) -> ImportResult:
    """Parse several yearly exports, given as (name, text) pairs. Console limits each
    download to 365 days, so overlapping files are common; identical trades count once."""
    return parse_tradebooks(files, PROFILE)


def load_zerodha_tradebook(data: bytes, *, name: str = "tradebook",
                        password: str | None = None) -> ImportResult:
    """CSV or XLSX bytes."""
    return load_tradebook(data, PROFILE, name=name, password=password)


def load_zerodha_tradebooks(files: Iterable[tuple[str, bytes]], *,
                         password: str | None = None) -> ImportResult:
    return load_tradebooks(files, PROFILE, password=password)
