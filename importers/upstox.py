"""Upstox tradebook (CSV) importer.

FORMAT UNCONFIRMED (docs/OPEN_QUESTIONS.md Q-016). Upstox documents the fields of its trade
history API: exchange (NSE, NFO, MCX, CDS, BMF), segment (EQ, FO, CD, COM, MF), option_type
(CE, PE), quantity, amount, trade_id, trade_date (YYYY-mm-dd), transaction_type (BUY, SELL),
scrip_name, strike_price, expiry (YYYY-mm-dd), price, isin (EQ, MF), symbol (EQ, FO):
https://upstox.com/developer/api-documentation/get-historical-trades/
For F&O, ``symbol`` is the underlying, so the contract is built from symbol + expiry + strike +
option type. The downloadable trade report is assumed to use the same names. Secondary sources
mention "Date", "Side", "Trade Num" and "Trade Time", accepted as lower-priority aliases;
without a segment column the segment is guessed from the exchange, with a warning. Only EQ and
FO rows are imported; charges and STT are not in the report.
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

PROFILE = BrokerProfile(
    key="UPSTOX",
    source="Upstox tradebook (CSV)",
    columns={
        "trade_date": ("trade_date", "date"),
        "side": ("transaction_type", "side"),
        "quantity": ("quantity",),
        "price": ("price",),
        "isin": ("isin",),
        "symbol": ("symbol",),
        "segment": ("segment",),
        "exchange": ("exchange",),
        "trade_id": ("trade_id", "trade_num"),
        "executed_at": ("trade_time",),
        "expiry": ("expiry",),
        "strike": ("strike_price",),
        "option_type": ("option_type",),
    },
    required=("trade_date", "side", "quantity", "price", "isin", "symbol", "exchange",
              "trade_id"),
    segments={"EQ": Segment.EQUITY, "FO": Segment.FNO},
    sides={"BUY": Side.BUY, "SELL": Side.SELL},
    confirmed=False,
    notes=(
        "Upstox tradebook format is unconfirmed (docs/OPEN_QUESTIONS.md Q-016); column names "
        "are taken from Upstox's trade-history API documentation.",
        "The tradebook has no brokerage, charges or STT; they were set to zero, which slightly "
        "overstates gains.",
    ),
    question="Q-016",
    infer_segment=True,
    compose_contract=True,
)


def parse_upstox_tradebook(text: str, *, name: str = "tradebook") -> ImportResult:
    return parse_tradebook(text, PROFILE, name=name)


def parse_upstox_tradebooks(files: Iterable[tuple[str, str]]) -> ImportResult:
    return parse_tradebooks(files, PROFILE)


def load_upstox_tradebook(data: bytes, *, name: str = "tradebook",
                        password: str | None = None) -> ImportResult:
    """CSV or XLSX bytes."""
    return load_tradebook(data, PROFILE, name=name, password=password)


def load_upstox_tradebooks(files: Iterable[tuple[str, bytes]], *,
                         password: str | None = None) -> ImportResult:
    return load_tradebooks(files, PROFILE, password=password)
