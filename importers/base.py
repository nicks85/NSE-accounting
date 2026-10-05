"""Shared importer types and strict parsers. Amounts go straight from text to ``Decimal``,
never through float (CLAUDE.md rule 2)."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from engine.models import Trade


class ImportFormatError(ValueError):
    """The file doesn't match the expected format. Messages name the row and column."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    source: str
    trades: tuple[Trade, ...]
    warnings: tuple[str, ...]
    format_confirmed: bool
    """False while the column layout is based on secondary sources (see OPEN_QUESTIONS)."""


def normalise_header(name: str) -> str:
    """'Order Execution Time' / 'order_execution_time' / ' TRADE-DATE ' → snake_case."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def parse_decimal(text: str, *, where: str) -> Decimal:
    cleaned = text.strip().replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ImportFormatError(f"{where}: not a number: {text!r}") from None
    if not value.is_finite():
        raise ImportFormatError(f"{where}: not a finite number: {text!r}")
    return value


DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")


def parse_date(text: str, *, where: str) -> date:
    cleaned = text.strip()
    # Accept a timestamp too: keep the date part only.
    cleaned = cleaned.split("T")[0].split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ImportFormatError(f"{where}: unrecognised date {text!r}")
