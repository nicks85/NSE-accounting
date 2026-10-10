"""Opening holdings: shares held before the first imported tradebook (brief 0001 D5 option a).

Each row is one lot: ISIN, optional name, quantity, purchase date, price per share, optional
charges (excluding STT) and how the shares were acquired. Rows come from Kosh's CSV template or
from the form on the Import screen, and become ordinary purchases that FIFO matches like any
other buy. They are never intraday.

What to enter for each way of acquiring shares is a best guess until confirmed (Q-029):
an IPO uses the allotment date and issue price, bonus shares the allotment date and price 0,
a gift or inheritance the previous owner's date and cost (which also places the lot by that
date in FIFO order), ESOP shares the value taxed as salary with the exercise or allotment date
(which one is still open), and a move from another demat account the original date and cost.

A lot with the same ISIN, date, quantity and price as one already saved is the same lot. Two
genuinely identical lots are entered in one save (or as one lot with the total quantity).

Mutual-fund units held with the fund house aren't entered here: the CAS carries their full
history.
"""

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from engine.classify.trades import OPENING_PREFIX
from engine.ledger.settings import HOW_ACQUIRED
from engine.models import Segment, Side, Trade
from importers.base import (
    ImportFormatError,
    is_ambiguous_date,
    is_valid_isin,
    parse_date,
    parse_decimal,
)
from importers.tabular import decode_csv

COLUMNS = ("isin", "name", "quantity", "buy_date", "price", "charges", "how_acquired")
REQUIRED = ("isin", "quantity", "buy_date", "price")
SOURCE = "Opening holdings"


@dataclass(frozen=True, slots=True)
class OpeningHoldings:
    trades: tuple[Trade, ...]
    how_acquired: Mapping[str, str]
    """Trade id → how the shares were acquired (Q-029)."""
    names: Mapping[str, str]
    """ISIN → name, where the row gave one."""
    warnings: tuple[str, ...] = ()


def template_csv() -> str:
    """The CSV template, with a header and one synthetic example row to replace."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerow(["INE000A01012", "EXAMPLE LTD (replace this row)", "100", "2016-04-01",
                     "245.50", "12.40", "bought"])
    return out.getvalue()


def parse_rows(rows: Iterable[Mapping[str, str]], *, today: date,
               where: str = "row", warnings: Iterable[str] = ()) -> OpeningHoldings:
    """Check and convert rows (column name → text) into purchases."""
    trades: list[Trade] = []
    how: dict[str, str] = {}
    names: dict[str, str] = {}
    notes = list(warnings)
    seen: dict[tuple[str, str, str, str], int] = {}
    for number, row in enumerate(rows, start=1):
        at = f"{where} {number}"
        cell = {k: str(row.get(k) or "").strip() for k in COLUMNS}
        if not any(cell.values()):
            continue
        missing = [k for k in REQUIRED if not cell[k]]
        if missing:
            raise ImportFormatError(f"{at}: missing {', '.join(missing)}")
        isin = cell["isin"].upper()
        if not is_valid_isin(isin):
            raise ImportFormatError(f"{at}: {isin} isn't a valid Indian ISIN (check for a typo)")
        if is_ambiguous_date(cell["buy_date"].replace("-", "/")):
            # A US-format re-save (4/1/2016) would silently move the holding period and the
            # 31-Jan-2018 grandfathering cut-off: refuse rather than guess.
            raise ImportFormatError(f"{at}: date {cell['buy_date']!r} could be day/month or "
                                    "month/day; write it as YYYY-MM-DD")
        bought = parse_date(cell["buy_date"], where=f"{at} buy_date")
        if bought > today:
            raise ImportFormatError(f"{at}: purchase date {bought} is in the future")
        quantity = parse_decimal(cell["quantity"], where=f"{at} quantity")
        price = parse_decimal(cell["price"], where=f"{at} price")
        charges = parse_decimal(cell["charges"] or "0", where=f"{at} charges")
        acquired = (cell["how_acquired"] or "bought").lower()
        if acquired not in HOW_ACQUIRED:
            raise ImportFormatError(
                f"{at}: how_acquired must be one of {', '.join(HOW_ACQUIRED)}, not {acquired!r}")
        if price == 0 and acquired != "bonus":
            raise ImportFormatError(f"{at}: price 0 is only for bonus shares")
        if quantity != quantity.to_integral_value():
            notes.append(f"{at}: fractional share quantity {quantity}")
        lot = (isin, bought.isoformat(), str(quantity.normalize()), str(price.normalize()))
        seen[lot] = seen.get(lot, 0) + 1
        trade_id = f"{OPENING_PREFIX}{':'.join(lot)}:{seen[lot]}"
        try:
            trades.append(Trade(trade_id, bought, isin, Side.BUY, quantity, price,
                                charges=charges, segment=Segment.EQUITY))
        except ValueError as error:
            raise ImportFormatError(f"{at}: {error}") from None
        how[trade_id] = acquired
        if cell["name"]:
            names[isin] = cell["name"]
    if not trades:
        raise ImportFormatError(f"{where}s: no holdings to add")
    return OpeningHoldings(tuple(trades), how, names, tuple(notes))


def load_opening_csv(data: bytes, *, name: str, today: date) -> OpeningHoldings:
    """Read Kosh's opening-holdings CSV template."""
    text, encoding_note = decode_csv(data, name)
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in reader.fieldnames or []]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ImportFormatError(
            f"{name}: not Kosh's opening-holdings template (no {', '.join(missing)} column)")
    rows = ({k.strip().lower(): v for k, v in r.items() if k} for r in reader)
    return parse_rows(rows, today=today, where=f"{name} row",
                      warnings=[encoding_note] if encoding_note else [])
