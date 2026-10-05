"""Shared engine for tradebook-style CSV files. A ``BrokerProfile`` says which header names
mean what; everything else (header-row search, strict parsing, ordering, de-duplication,
warnings) is common so every broker gets the same safeguards."""

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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

FIELDS = ("trade_date", "side", "quantity", "price", "isin", "symbol", "segment", "exchange",
          "trade_id", "executed_at", "auction", "expiry", "strike", "option_type")
"""Canonical fields a profile can map. Required ones are listed per profile."""

INFERRED_SEGMENTS = {"NSE": Segment.EQUITY, "BSE": Segment.EQUITY,
                     "NFO": Segment.FNO, "BFO": Segment.FNO}
"""Exchange → segment guess, used only by profiles that opt in and only without a segment
column."""
HEADER_SEARCH_ROWS = 30
"""Reports often have title rows above the header; search this many rows for it."""


@dataclass(frozen=True, slots=True)
class BrokerProfile:
    key: str
    """Short id used in trade ids, e.g. ZERODHA."""
    source: str
    columns: Mapping[str, tuple[str, ...]]
    """Canonical field → accepted header names (compared after ``normalise_header``)."""
    required: tuple[str, ...]
    segments: Mapping[str, Segment]
    sides: Mapping[str, Side]
    confirmed: bool
    notes: tuple[str, ...]
    question: str
    """docs/OPEN_QUESTIONS.md id describing what is unconfirmed."""
    infer_segment: bool = False
    """When the file has no segment column, guess it from the exchange (with a warning)."""
    compose_contract: bool = False
    """F&O instrument = symbol + expiry + strike + option type (for files where ``symbol`` is
    only the underlying)."""


def _find_header(rows: list[list[str]], profile: BrokerProfile) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        names: dict[str, list[int]] = {}
        for i, cell in enumerate(row):
            if cell.strip():
                names.setdefault(normalise_header(cell), []).append(i)
        found: dict[str, int] = {}
        for field, aliases in profile.columns.items():
            alias = next((a for a in aliases if a in names), None)  # first alias wins
            if alias is not None:
                if len(names[alias]) > 1:
                    found[field] = -1
                else:
                    found[field] = names[alias][0]
        if all(field in found for field in profile.required):
            duplicated = [f for f, i in found.items() if i < 0]
            if duplicated:
                raise ImportFormatError(
                    f"header row {index + 1}: column(s) for {', '.join(duplicated)} appear more "
                    "than once"
                )
            return index, found
    raise ImportFormatError(
        f"not a {profile.source}: no header row with the column(s) "
        f"{', '.join(profile.required)} in the first {HEADER_SEARCH_ROWS} rows"
    )


def _order_key(trade: Trade, index: int) -> tuple[date, bool, datetime, int]:
    """Date, then execution time when known, then input position."""
    return (trade.trade_date, trade.executed_at is None,
            trade.executed_at or datetime.min, index)


def same_trade(a: Trade, b: Trade) -> bool:
    fields = ("trade_date", "instrument", "side", "quantity", "price", "segment")
    return all(getattr(a, f) == getattr(b, f) for f in fields)


def _contract(symbol: str, expiry: str, strike: str, option_type: str, *, where: str) -> str:
    """Unique F&O contract id from separate fields: SYMBOL:YYYY-MM-DD:FUT or :STRIKE:CE/PE."""
    if not expiry:
        raise ImportFormatError(f"{where}: missing expiry for F&O trade")
    expiry_date = parse_date(expiry, where=f"{where} expiry").isoformat()
    kind = option_type.upper()
    if kind in {"CE", "PE"}:
        strike_value = parse_decimal(strike, where=f"{where} strike").normalize()
        return f"{symbol}:{expiry_date}:{strike_value:f}:{kind}"
    if kind or (strike and parse_decimal(strike, where=f"{where} strike") != 0):
        raise ImportFormatError(f"{where}: unrecognised option type {option_type!r}")
    return f"{symbol}:{expiry_date}:FUT"


def parse_tradebook(text: str, profile: BrokerProfile, *, name: str = "tradebook") -> ImportResult:
    """Parse one CSV export. ``name`` labels warnings and errors."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not any(any(c.strip() for c in row) for row in rows):
        raise ImportFormatError(f"{name}: file is empty")
    try:
        header_index, columns = _find_header(rows, profile)
    except ImportFormatError as error:
        raise ImportFormatError(f"{name}: {error}") from None
    required_width = max(columns[f] for f in profile.required) + 1
    has_segment = "segment" in columns
    guessed = skipped = 0

    trades: list[Trade] = []
    warnings: list[str] = list(profile.notes)
    by_key: dict[tuple[str, str, str], Trade] = {}
    untimed = ambiguous = 0
    for line_no, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        where = f"{name} row {line_no}"
        if len(row) < required_width:
            raise ImportFormatError(
                f"{where}: {len(row)} columns, expected at least {required_width}")

        def cell(field: str, row: list[str] = row) -> str:
            index = columns.get(field)
            return row[index].strip() if index is not None and index < len(row) else ""

        exchange = cell("exchange").upper()
        if has_segment:
            code = cell("segment").upper()
            segment = profile.segments.get(code)
        elif profile.infer_segment:
            code = exchange
            segment = INFERRED_SEGMENTS.get(exchange)
            guessed += segment is not None
        else:  # pragma: no cover - profiles either require a segment or infer it
            code, segment = "", None
        if segment is None:
            warnings.append(f"{where}: segment {code!r} not supported yet; row skipped")
            skipped += 1
            continue
        side = profile.sides.get(cell("side").upper())
        if side is None:
            raise ImportFormatError(f"{where}: trade type {cell('side')!r} is not buy/sell")
        id_field = "isin" if segment is Segment.EQUITY else "symbol"
        instrument = cell(id_field).upper()
        if not instrument:
            raise ImportFormatError(f"{where}: missing {id_field}")
        if segment is Segment.FNO and profile.compose_contract:
            instrument = _contract(instrument, cell("expiry"), cell("strike"),
                                   cell("option_type"), where=where)
        trade_number = cell("trade_id")
        if not trade_number:
            raise ImportFormatError(f"{where}: missing trade_id")
        raw_date = cell("trade_date")
        trade_date = parse_date(raw_date, where=f"{where} trade_date")
        ambiguous += is_ambiguous_date(raw_date)
        quantity = parse_decimal(cell("quantity"), where=f"{where} quantity")
        price = parse_decimal(cell("price"), where=f"{where} price")
        executed_at = parse_datetime(cell("executed_at"), trade_date)
        untimed += executed_at is None
        if segment is Segment.EQUITY and quantity != quantity.to_integral_value():
            warnings.append(f"{where}: fractional share quantity {quantity}")
        if cell("auction").lower() in {"true", "yes", "1"}:
            warnings.append(f"{where}: auction trade imported as a normal trade "
                            f"({profile.question})")
        # Exchange trade numbers are only unique within a trading day (assumption).
        key = (exchange or "-", trade_date.isoformat(), trade_number)
        try:
            trade = Trade(
                trade_id=f"{profile.key}:" + ":".join(key),
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
            if not same_trade(by_key[key], trade):
                raise ImportFormatError(
                    f"{where}: trade {trade_number} on {key[0]} {key[1]} appears twice with "
                    "different details"
                )
            warnings.append(f"{where}: duplicate trade {trade_number} on {key[0]} {key[1]} skipped")
            continue
        by_key[key] = trade
        trades.append(trade)

    if skipped and not trades:
        raise ImportFormatError(
            f"{name}: none of the {skipped} row(s) could be imported (unsupported segments); "
            "check the segment codes"
        )
    if guessed:
        warnings.append(f"{name}: no segment column; {guessed} row(s) classified as equity or "
                        f"F&O from the exchange name, so mutual-fund or currency rows on NSE/BSE "
                        f"may be misread ({profile.question})")
    if untimed:
        warnings.append(f"{name}: {untimed} row(s) without a readable execution time; same-day "
                        "order follows the file")
    if ambiguous:
        warnings.append(f"{name}: {ambiguous} date(s) like 05/01/2025 were read as day/month; "
                        f"check they weren't re-saved in month/day order ({profile.question})")
    order = sorted(range(len(trades)), key=lambda i: _order_key(trades[i], i))
    return ImportResult(profile.source, tuple(trades[i] for i in order), tuple(warnings),
                        profile.confirmed)


def parse_tradebooks(files: Iterable[tuple[str, str]], profile: BrokerProfile) -> ImportResult:
    """Parse several exports given as (name, text); identical trades across files count once."""
    parts = [parse_tradebook(text, profile, name=name) for name, text in files]
    return merge_results(parts, profile.source, profile.confirmed)


def merge_results(parts: list[ImportResult], source: str, confirmed: bool) -> ImportResult:
    merged: dict[str, Trade] = {}
    repeats = 0
    for trade in (t for p in parts for t in p.trades):
        existing = merged.get(trade.trade_id)
        if existing is None:
            merged[trade.trade_id] = trade
        elif same_trade(existing, trade):
            repeats += 1
        else:
            raise ImportFormatError(f"{trade.trade_id} appears in two files with different details")
    trades = list(merged.values())
    order = sorted(range(len(trades)), key=lambda i: _order_key(trades[i], i))
    warnings = list(dict.fromkeys(w for p in parts for w in p.warnings))
    if repeats:
        warnings.append(f"{repeats} trade(s) appeared in more than one file and were counted once")
    return ImportResult(source, tuple(trades[i] for i in order), tuple(warnings), confirmed)
