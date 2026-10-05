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

from engine.models import Segment, Side, Trade
from importers.base import (
    ImportFormatError,
    ImportResult,
    normalise_header,
    parse_date,
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


def parse_zerodha_tradebook(text: str) -> ImportResult:
    """Parse the text of a Zerodha tradebook CSV export."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportFormatError("file is empty") from None
    columns = {normalise_header(name): i for i, name in enumerate(header)}
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise ImportFormatError(
            f"not a Zerodha tradebook: missing column(s) {', '.join(missing)} "
            f"(found: {', '.join(header)})"
        )

    rows: list[tuple[str, int, Trade]] = []
    warnings: list[str] = list(NOTES)
    seen: set[tuple[str, str, str]] = set()
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(name: str, row: list[str] = row) -> str:
            index = columns.get(name)
            return row[index].strip() if index is not None and index < len(row) else ""

        where = f"row {line_no}"
        segment_code = cell("segment").upper()
        if segment_code not in SEGMENTS:
            warnings.append(f"{where}: segment {segment_code!r} not supported yet; row skipped")
            continue
        segment = SEGMENTS[segment_code]
        side = SIDES.get(cell("trade_type").upper())
        if side is None:
            raise ImportFormatError(f"{where}: trade_type {cell('trade_type')!r} is not buy/sell")
        id_column = "isin" if segment is Segment.EQUITY else "symbol"
        instrument = cell(id_column)
        if not instrument:
            raise ImportFormatError(f"{where}: missing {id_column}")
        trade_date = parse_date(cell("trade_date"), where=f"{where} trade_date")
        # Exchange trade numbers are only unique within a trading day (assumption, Q-015).
        key = (cell("exchange").upper(), trade_date.isoformat(), cell("trade_id"))
        if key in seen:
            warnings.append(f"{where}: duplicate trade {key[2]} on {key[0]} {key[1]} skipped")
            continue
        seen.add(key)
        quantity = parse_decimal(cell("quantity"), where=f"{where} quantity")
        price = parse_decimal(cell("price"), where=f"{where} price")
        try:
            trade = Trade(
                trade_id="ZERODHA:" + ":".join(key),
                trade_date=trade_date,
                instrument=instrument,
                side=side,
                quantity=quantity,
                price=price,
                segment=segment,
            )
        except ValueError as error:
            raise ImportFormatError(f"{where}: {error}") from None
        rows.append((cell("order_execution_time"), line_no, trade))

    # Same-day order matters for intraday netting: sort by date, then execution time.
    rows.sort(key=lambda r: (r[2].trade_date, r[0], r[1]))
    return ImportResult(SOURCE, tuple(t for _, _, t in rows), tuple(warnings), FORMAT_CONFIRMED)


def parse_zerodha_tradebooks(texts: Iterable[str]) -> ImportResult:
    """Parse several yearly exports (Console limits each download to 365 days)."""
    parts = [parse_zerodha_tradebook(t) for t in texts]
    trades = sorted((t for p in parts for t in p.trades), key=lambda t: t.trade_date)
    ids = [t.trade_id for t in trades]
    unique = list({t.trade_id: t for t in trades}.values())
    warnings = list(dict.fromkeys(w for p in parts for w in p.warnings))
    if len(unique) != len(ids):
        warnings.append(f"{len(ids) - len(unique)} trade(s) appeared in more than one file and "
                        "were counted once")
    return ImportResult(SOURCE, tuple(unique), tuple(warnings), FORMAT_CONFIRMED)
