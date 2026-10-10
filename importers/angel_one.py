"""Angel One trade history ("Trades and Charges") importer, XLSX or CSV.

Layout read from a real export supplied by the user (the file is not committed; synthetic
fixtures only, docs/OPEN_QUESTIONS.md Q-030):

- sheet ``TradesAndCharges``; a block of client details, date range and a "Charges Summary"
  (``Total Trade Charges``, ``Total Non Trade Charges`` and their breakdowns) above a
  "TradeBook And Charges" title, then the header row (row 35 in the sample);
- columns Scrip/Contract, Buy/Sell, Buy Price, Sell Price, Quantity, Brokerage, GST, STT,
  Sebi Tax, Exchange Turnover Charges, Stamp Duty, Other Charges, IPFT Charges, Order Type,
  Segment, Exchange, Order ID, Trade ID, Date;
- one row per trade (fill), several rows per Order ID; the price is in Buy Price for buys and
  Sell Price for sells; Date is ``YYYY-MM-DD`` with no time; charges are given per row.

Only Segment ``CAPITAL`` (cash equity) is imported: the sample had no F&O rows, so their
layout is unknown. The file has no ISIN and truncates names to about 20 characters. A scrip is
keyed by its ISIN when ``isin_map`` knows the name, otherwise by ``NAME:<scrip name>``, so the
file imports without asking the user anything (docs/decisions/0002-import-without-isin.md).

Charges other than STT become the trade's ``charges`` (cost of a buy, transfer expense of a
sell); STT is kept separately (``COMPUTATION`` / ``STT_BUSINESS_DEDUCTION`` in
engine/rules/common.py; open points in Q-027).
"""

import csv
import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from engine.identifiers import scrip_key as scrip_key  # re-exported: tests and callers use it here
from engine.models import CHARGE_KINDS, Segment, Side, Trade
from engine.money import ZERO
from importers.base import (
    ImportFormatError,
    ImportResult,
    is_valid_isin,
    normalise_header,
    parse_date,
    parse_decimal,
)
from importers.tabular import (
    BrokerProfile,
    decode_csv,
    find_header,
    merge_results,
    same_trade,
)
from importers.xlsx import is_xlsx, read_xlsx_sheet

SOURCE = "Angel One trade history"
SHEET = "TradesAndCharges"
HEADER_SEARCH_ROWS = 100
"""The header sits below a summary block (row 35 in the sample)."""
CHARGES_TOLERANCE = Decimal(1)
"""The file's own summary is rounded (STT to whole rupees), so allow ₹1 of difference."""

CHARGE_COLUMNS = ("brokerage", "gst", "sebi_tax", "exchange_turnover_charges", "stamp_duty",
                  "other_charges", "ipft_charges")
"""Per-trade charges other than STT."""
CHARGE_KIND = {"brokerage": "BROKERAGE", "gst": "GST", "sebi_tax": "SEBI",
               "exchange_turnover_charges": "EXCHANGE", "stamp_duty": "STAMP",
               "other_charges": "OTHER", "ipft_charges": "IPFT"}
"""Each charge column's type in the ledger (brief 0005)."""
NON_TRADE_LABELS = ("dp_charges", "interest_charges", "monthly_account_maintenance",
                    "pledge_charges", "call_and_trade_charges", "margin_shortfall_penalty")

PROFILE = BrokerProfile(
    key="ANGELONE",
    source=SOURCE,
    columns={
        "scrip": ("scrip_contract",),
        "side": ("buy_sell",),
        "buy_price": ("buy_price",),
        "sell_price": ("sell_price",),
        "quantity": ("quantity",),
        "stt": ("stt",),
        **{c: (c,) for c in CHARGE_COLUMNS},
        "order_type": ("order_type",),
        "segment": ("segment",),
        "exchange": ("exchange",),
        "order_id": ("order_id",),
        "trade_id": ("trade_id",),
        "trade_date": ("date",),
    },
    required=("scrip", "side", "buy_price", "sell_price", "quantity", "segment", "exchange",
              "trade_id", "trade_date"),
    segments={"CAPITAL": Segment.EQUITY},
    sides={"BUY": Side.BUY, "SELL": Side.SELL},
    confirmed=True,
    notes=(),
    question="Q-030",
)


NAME_PREFIX = "NAME:"
"""Instrument key for a scrip whose ISIN isn't known: ``NAME:`` + its ``scrip_key``."""
FUND_LIKE = re.compile(r"ETF|BEES|FUND|GOLD|SILVER|LIQUID|MON100|MAFANG")
"""Text anywhere in a name that suggests an ETF or fund, which is taxed differently from a share
(Q-024, Q-035). Matched anywhere (GOLDBEES, SETFNIF50): a share whose name merely contains one
of these gets an unneeded warning, which is the safer error."""


@dataclass(frozen=True, slots=True)
class _Parsed:
    result: ImportResult
    names: dict[str, str]
    """Instrument → scrip name as written in the file."""
    scrips: dict[str, str]
    """Trade id → scrip name, for every trade (the ledger keeps it, brief 0007)."""


def _summary(rows: list[list[str]]) -> dict[str, str]:
    """Label → value pairs from the rows above the header (first two cells of each row)."""
    pairs: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            pairs.setdefault(normalise_header(row[0]), row[1].strip())
    return pairs


def _amount(text: str, *, where: str) -> Decimal:
    return parse_decimal(text, where=where) if text.strip() else ZERO


def _parse_rows(rows: list[list[str]], isin_map: Mapping[str, str], name: str) -> _Parsed:
    if not any(any(c.strip() for c in row) for row in rows):
        raise ImportFormatError(f"{name}: file is empty")
    try:
        header_index, columns = find_header(rows, PROFILE, limit=HEADER_SEARCH_ROWS)
    except ImportFormatError as error:
        raise ImportFormatError(f"{name}: {error}") from None
    summary = _summary(rows[:header_index])

    trades: list[Trade] = []
    warnings: list[str] = []
    names: dict[str, str] = {}
    scrips: dict[str, str] = {}
    by_key: dict[tuple[str, str, str], Trade] = {}
    charges_in_rows = ZERO
    skipped: dict[str, int] = {}
    order_types: dict[str, int] = {}
    for line_no, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        where = f"{name} row {line_no}"

        def cell(field: str, row: list[str] = row) -> str:
            index = columns.get(field)
            return row[index].strip() if index is not None and index < len(row) else ""

        stt = _amount(cell("stt"), where=f"{where} STT")
        found = {CHARGE_KIND[c]: amount for c in CHARGE_COLUMNS
                 if (amount := _amount(cell(c), where=f"{where} {c}"))}
        # In the ledger's order of types, so a trade reads back exactly as imported.
        parts = tuple((kind, found[kind]) for kind in CHARGE_KINDS if kind in found)
        charges = sum((amount for _, amount in parts), ZERO)
        charges_in_rows += charges + stt  # duplicates are taken out again below
        segment_code = cell("segment").upper()
        segment = PROFILE.segments.get(segment_code)
        if segment is None:
            skipped[segment_code] = skipped.get(segment_code, 0) + 1
            continue
        order_type = cell("order_type")
        if order_type.upper() != "DELIVERY":
            order_types[order_type] = order_types.get(order_type, 0) + 1
        side = PROFILE.sides.get(cell("side").upper())
        if side is None:
            raise ImportFormatError(f"{where}: Buy/Sell {cell('side')!r} is not buy or sell")
        price_field = "buy_price" if side is Side.BUY else "sell_price"
        if not cell(price_field):
            raise ImportFormatError(
                f"{where}: {side.value.lower()} with no {price_field.replace('_', ' ')}")
        price = parse_decimal(cell(price_field), where=f"{where} {price_field}")
        quantity = parse_decimal(cell("quantity"), where=f"{where} quantity")
        if quantity != quantity.to_integral_value():
            warnings.append(f"{where}: fractional share quantity {quantity}")
        scrip = scrip_key(cell("scrip"))
        if not scrip:
            raise ImportFormatError(f"{where}: missing Scrip/Contract")
        instrument = isin_map.get(scrip) or NAME_PREFIX + scrip
        names.setdefault(instrument, scrip)
        trade_number = cell("trade_id")
        if not trade_number:
            raise ImportFormatError(f"{where}: missing Trade ID")
        trade_date = parse_date(cell("trade_date"), where=f"{where} Date")
        exchange = cell("exchange").upper() or "-"
        # Exchange trade numbers are only unique within a trading day (as for other brokers).
        key = (exchange, trade_date.isoformat(), trade_number)
        try:
            trade = Trade(
                trade_id=f"{PROFILE.key}:" + ":".join(key),
                trade_date=trade_date,
                instrument=instrument,
                side=side,
                quantity=quantity,
                price=price,
                charges=charges,
                stt=stt,
                segment=segment,
                charge_parts=parts,
            )
        except ValueError as error:
            raise ImportFormatError(f"{where}: {error}") from None
        if key in by_key:
            if not same_trade(by_key[key], trade):
                raise ImportFormatError(
                    f"{where}: trade {trade_number} on {key[0]} {key[1]} appears twice with "
                    "different details")
            warnings.append(f"{where}: duplicate trade {trade_number} on {key[0]} {key[1]} skipped")
            charges_in_rows -= charges + stt
            continue
        by_key[key] = trade
        trades.append(trade)
        scrips[trade.trade_id] = scrip

    for code, count in skipped.items():
        warnings.append(f"{name}: {count} row(s) in segment {code!r} skipped; only cash equity "
                        "(CAPITAL) is supported for Angel One so far (Q-030)")
    for order_type, count in order_types.items():
        warnings.append(f"{name}: {count} row(s) with order type {order_type!r} imported as "
                        "cash equity; same-day buy and sell are treated as intraday (Q-030)")
    if trades:
        warnings.append(f"{name}: the file has no trade times; trades on the same day are "
                        "matched in file order")
    warnings.extend(_check_charges(summary, charges_in_rows, name))
    return _Parsed(ImportResult(SOURCE, tuple(trades), tuple(warnings), True,
                                _summary_amount(summary, "total_trade_charges"),
                                charges_in_rows), names, scrips)


def _summary_amount(summary: dict[str, str], label: str) -> Decimal | None:
    """A summary figure, or None when it's absent or not a number (e.g. "-"): the summary is
    only used for checks, so an unreadable cell must not stop the import."""
    try:
        return parse_decimal(summary[label], where=label) if label in summary else None
    except ImportFormatError:
        return None


def _check_charges(summary: dict[str, str], in_rows: Decimal, name: str) -> list[str]:
    notes: list[str] = []
    stated = _summary_amount(summary, "total_trade_charges")
    if stated is not None:
        total = stated
        if abs(total - in_rows) > CHARGES_TOLERANCE:
            notes.append(f"{name}: charges on the trade rows add up to ₹{in_rows:.2f}, but the "
                         f"file's Total Trade Charges is ₹{total:.2f}; check the file is "
                         "complete")
    non_trade = {label: _summary_amount(summary, label) for label in NON_TRADE_LABELS}
    charged = {label: amount for label, amount in non_trade.items() if amount}
    if charged:
        listed = ", ".join(f"{label.replace('_', ' ')} ₹{amount:.2f}"
                           for label, amount in charged.items())
        notes.append(f"{name}: the file lists charges not tied to a trade ({listed}); they are "
                     "not deducted from any gain (Q-027)")
    return notes


def _rows(data: bytes, name: str, password: str | None) -> tuple[list[list[str]], list[str]]:
    if is_xlsx(data):
        try:
            try:
                book = read_xlsx_sheet(data, password=password, sheet=SHEET)
            except ImportFormatError:
                book = read_xlsx_sheet(data, password=password)
        except ImportFormatError as error:
            raise ImportFormatError(f"{name}: {error}") from None
        return book.rows, [f"{name}: {w}" for w in book.warnings]
    text, note = decode_csv(data, name)
    try:
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    except csv.Error as error:
        raise ImportFormatError(f"{name}: not a readable CSV file ({error})") from None
    return rows, [note] if note else []


def _checked_map(isin_map: Mapping[str, str]) -> dict[str, str]:
    checked: dict[str, str] = {}
    for raw_name, raw_isin in isin_map.items():
        isin = raw_isin.strip().upper()
        if not is_valid_isin(isin):
            raise ImportFormatError(
                f"{raw_name}: {raw_isin!r} is not a valid ISIN (12 characters starting with IN; "
                "the last digit is a check digit, so a typo is caught)")
        checked[scrip_key(raw_name)] = isin
    return checked


@dataclass(frozen=True, slots=True)
class AngelOneImport:
    result: ImportResult
    names: dict[str, str]
    """Instrument → scrip name as written in the file (for display and Schedule 112A)."""
    scrips: dict[str, str]
    """Trade id → scrip name as written, for every trade, including those whose ISIN came
    from ``isin_map``: the ledger keeps it, so a confirmed name can be undone (brief 0007)."""


def load_angel_one_tradebooks(files: Iterable[tuple[str, bytes]], *,
                              isin_map: Mapping[str, str] | None = None,
                              password: str | None = None) -> AngelOneImport:
    """Load one or more Angel One trade-history files given as (name, bytes). Scrips not in
    ``isin_map`` are keyed by name; each such scrip gets one note, and names that look like an
    ETF or fund get a warning."""
    checked = _checked_map(isin_map or {})
    parts: list[ImportResult] = []
    names: dict[str, str] = {}
    scrips: dict[str, str] = {}
    for name, data in files:
        rows, notes = _rows(data, name, password)
        parsed = _parse_rows(rows, checked, name)
        names.update({k: v for k, v in parsed.names.items() if k not in names})
        scrips.update(parsed.scrips)
        parts.append(ImportResult(parsed.result.source, parsed.result.trades,
                                  (*parsed.result.warnings, *notes), True,
                                  parsed.result.stated_charges, parsed.result.row_charges))
    merged = merge_results(parts, SOURCE, True)
    by_name = sorted(v for k, v in names.items() if k.startswith(NAME_PREFIX))
    extra: list[str] = []
    if by_name:
        extra.append(f"{len(by_name)} compan{'y' if len(by_name) == 1 else 'ies'} imported by "
                     "name (the file has no ISIN). Gains are unaffected; Schedule 112A needs the "
                     "ISIN only for shares bought on or before 31-Jan-2018.")
    extra += [f"{scrip} looks like an ETF or fund; without its ISIN Kosh taxes it as a listed "
              "share, which is wrong for gold, debt or international ETFs (Q-024)"
              for scrip in by_name if FUND_LIKE.search(scrip)]
    if not extra:
        return AngelOneImport(merged, names, scrips)
    return AngelOneImport(ImportResult(merged.source, merged.trades,
                                       (*merged.warnings, *extra), True, merged.stated_charges,
                                       merged.row_charges),
                          names, scrips)
