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


NUMBER = re.compile(
    r"-?(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d+)?", re.ASCII
)
"""Plain digits, Western grouping (1,234,567) or Indian grouping (12,34,567), optional decimals."""


def parse_decimal(text: str, *, where: str) -> Decimal:
    cleaned = text.strip()
    if not NUMBER.fullmatch(cleaned):
        raise ImportFormatError(f"{where}: not a number: {text!r}")
    try:
        return Decimal(cleaned.replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the regex only admits valid decimals
        raise ImportFormatError(f"{where}: not a number: {text!r}") from None


DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")
DATETIME_FORMATS = tuple(f"{d}{sep}%H:%M:%S" for d in DATE_FORMATS for sep in ("T", " "))


def is_ambiguous_date(text: str) -> bool:
    """dd/mm/yyyy where both parts are ≤ 12 could be a US-locale mm/dd/yyyy re-save."""
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/\d{4}", text.strip().split(" ")[0])
    if match is None:
        return False
    return int(match[1]) <= 12 and int(match[2]) <= 12 and match[1] != match[2]


def parse_datetime(text: str, on: date) -> datetime | None:
    """Execution timestamp, or a bare time (H:MM[:SS]) on ``on``; None when unparseable."""
    cleaned = text.strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.combine(on, datetime.strptime(cleaned, fmt).time())
        except ValueError:
            continue
    return None


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


INDIAN_ISIN = re.compile(r"IN[A-Z0-9]{9}[0-9]", re.ASCII)


def is_valid_isin(text: str) -> bool:
    """An Indian ISIN (``IN`` + 9 characters + check digit) whose check digit is right
    (ISO 6166: letters become 10-35, then the Luhn check over the digit string)."""
    if not INDIAN_ISIN.fullmatch(text):
        return False
    digits = "".join(str(int(c, 36)) for c in text)
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char) * (2 if position % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0
