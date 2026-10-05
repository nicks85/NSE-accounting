"""Date helpers for tax years and holding periods."""

import calendar
from datetime import date


def tax_year_of(day: date) -> int:
    """Start year of the Indian financial/tax year (April to March) containing ``day``."""
    return day.year if day.month >= 4 else day.year - 1


def tax_year_bounds(start_year: int) -> tuple[date, date]:
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def add_months(day: date, months: int) -> date:
    """Same day-of-month ``months`` later, clamped to the end of the month."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
