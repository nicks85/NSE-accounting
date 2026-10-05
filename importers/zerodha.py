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

import csv
import io
from collections.abc import Iterable
from datetime import date, datetime

from engine.models import Segment, Side, Trade
from importers.base import (
    ImportFormatError,
    ImportResult,
    is_ambiguous_date,
    normalise_header,
    parse_date,
    parse_datetime,
    parse_decimal,
)

SOURCE = "Zerodha tradebook (CSV)"
FORMAT_CONFIRMED = False
REQUIRED = ("symbol", "isin", "trade_date", "exchange", "segment", "trade_type", "quantity",
            "price", "trade_id", "order_id")
SEGMENTS = {"EQ": Segment.EQUITY, "FO": Segment.FNO}
SIDES = {"BUY": Side.BUY, "SELL": Side.SELL}

NOTES = (
    "Zerodha tradebook format is unconfirmed (docs/OPEN_QUESTIONS.md Q-015); check the "
    "imported trades against Console.",
    "The tradebook has no brokerage, charges or STT; they were set to zero, which slightly "
    "overstates gains. Import charges from contract notes or the Tax P&L when available.",
    "The tradebook excludes corporate actions, IPO/OFS allotments, buybacks and transfers "
    "from other brokers; add those separately or FIFO matching may fail.",
)


def _order_key(trade: Trade, index: int) -> tuple[date, bool, datetime, int]:
    """Date, then execution time when known, then input position."""
    return (trade.trade_date, trade.executed_at is None,
            trade.executed_at or datetime.min, index)


def _same_trade(a: Trade, b: Trade) -> bool:
    fields = ("trade_date", "instrument", "side", "quantity", "price", "segment")
    return all(getattr(a, f) == getattr(b, f) for f in fields)


def parse_zerodha_tradebook(text: str, *, name: str = "tradebook") -> ImportResult:
    """Parse the text of a Zerodha tradebook CSV export. ``name`` labels warnings."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportFormatError(f"{name}: file is empty") from None
    columns = {normalise_header(h): i for i, h in enumerate(header)}
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise ImportFormatError(
            f"{name}: not a Zerodha tradebook: missing column(s) {', '.join(missing)} "
            f"(found: {', '.join(header)})"
        )

    trades: list[Trade] = []
    warnings: list[str] = list(NOTES)
    by_key: dict[tuple[str, str, str], Trade] = {}
    untimed = ambiguous = 0
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        where = f"{name} row {line_no}"
        if len(row) < len(header):
            raise ImportFormatError(f"{where}: {len(row)} columns, expected {len(header)}")

        def cell(column: str, row: list[str] = row) -> str:
            index = columns.get(column)
            return row[index].strip() if index is not None else ""

        segment_code = cell("segment").upper()
        if segment_code not in SEGMENTS:
            warnings.append(f"{where}: segment {segment_code!r} not supported yet; row skipped")
            continue
        segment = SEGMENTS[segment_code]
        side = SIDES.get(cell("trade_type").upper())
        if side is None:
            raise ImportFormatError(f"{where}: trade_type {cell('trade_type')!r} is not buy/sell")
        id_column = "isin" if segment is Segment.EQUITY else "symbol"
        instrument = cell(id_column).upper()
        if not instrument:
            raise ImportFormatError(f"{where}: missing {id_column}")
        trade_number = cell("trade_id")
        if not trade_number:
            raise ImportFormatError(f"{where}: missing trade_id")
        raw_date = cell("trade_date")
        trade_date = parse_date(raw_date, where=f"{where} trade_date")
        ambiguous += is_ambiguous_date(raw_date)
        quantity = parse_decimal(cell("quantity"), where=f"{where} quantity")
        price = parse_decimal(cell("price"), where=f"{where} price")
        executed_at = parse_datetime(cell("order_execution_time"), trade_date)
        untimed += executed_at is None
        if segment is Segment.EQUITY and quantity != quantity.to_integral_value():
            warnings.append(f"{where}: fractional share quantity {quantity}")
        if cell("auction").lower() in {"true", "yes", "1"}:
            warnings.append(f"{where}: auction trade imported as a normal trade (Q-015)")
        # Exchange trade numbers are only unique within a trading day (assumption, Q-015).
        key = (cell("exchange").upper(), trade_date.isoformat(), trade_number)
        try:
            trade = Trade(
                trade_id="ZERODHA:" + ":".join(key),
                trade_date=trade_date,
                instrument=instrument,
                side=side,
                quantity=quantity,
                price=price,
                segment=segment,
                executed_at=executed_at,
            )
        except ValueError as error:
            raise ImportFormatError(f"{where}: {error}") from None
        if key in by_key:
            if not _same_trade(by_key[key], trade):
                raise ImportFormatError(
                    f"{where}: trade {trade_number} on {key[0]} {key[1]} appears twice with "
                    "different details"
                )
            warnings.append(f"{where}: duplicate trade {trade_number} on {key[0]} {key[1]} skipped")
            continue
        by_key[key] = trade
        trades.append(trade)

    if untimed:
        warnings.append(f"{name}: {untimed} row(s) without a readable execution time; same-day "
                        "order follows the file")
    if ambiguous:
        warnings.append(f"{name}: {ambiguous} date(s) like 05/01/2025 were read as day/month; "
                        "check they weren't re-saved in month/day order (Q-015)")
    order = sorted(range(len(trades)), key=lambda i: _order_key(trades[i], i))
    return ImportResult(SOURCE, tuple(trades[i] for i in order), tuple(warnings),
                        FORMAT_CONFIRMED)


def parse_zerodha_tradebooks(files: Iterable[tuple[str, str]]) -> ImportResult:
    """Parse several yearly exports, given as (name, text) pairs. Console limits each
    download to 365 days, so overlapping files are common; identical trades count once."""
    parts = [parse_zerodha_tradebook(text, name=name) for name, text in files]
    merged: dict[str, Trade] = {}
    repeats = 0
    for trade in (t for p in parts for t in p.trades):
        existing = merged.get(trade.trade_id)
        if existing is None:
            merged[trade.trade_id] = trade
        elif _same_trade(existing, trade):
            repeats += 1
        else:
            raise ImportFormatError(f"{trade.trade_id} appears in two files with different details")
    trades = list(merged.values())
    order = sorted(range(len(trades)), key=lambda i: _order_key(trades[i], i))
    warnings = list(dict.fromkeys(w for p in parts for w in p.warnings))
    if repeats:
        warnings.append(f"{repeats} trade(s) appeared in more than one file and were counted once")
    return ImportResult(SOURCE, tuple(trades[i] for i in order), tuple(warnings),
                        FORMAT_CONFIRMED)
