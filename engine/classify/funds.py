"""Mutual fund classes for capital gains. A fund's class depends on its portfolio, which a
statement doesn't show, so the class is supplied per ISIN (by the user, possibly pre-filled
from the scheme name and flagged UNVERIFIED, docs/OPEN_QUESTIONS.md Q-019).
"""

from enum import StrEnum

FOLIO_SEPARATOR = "#"
"""Fund lots are matched FIFO per folio: instrument = ISIN#FOLIO (Q-020)."""


class FundClass(StrEnum):
    EQUITY_ORIENTED = "equity-oriented"
    """At least 65% in domestic listed equity (or a fund of such funds at 90%/90%):
    2025 Act s.198(8); 1961 Act s.112A Explanation. Taxed like listed shares."""
    SPECIFIED = "specified"
    """More than 65% in debt and money-market instruments (or 65% in such funds):
    2025 Act s.76(5)(b); 1961 Act s.50AA. Units bought on or after 1-Apr-2023 are always
    short-term."""
    OTHER = "other"
    """Everything else: hybrid, international, gold, and specified units bought before
    1-Apr-2023."""


def fund_instrument(isin: str, folio: str) -> str:
    return f"{isin}{FOLIO_SEPARATOR}{folio}" if folio else isin


def isin_of(instrument: str) -> str:
    return instrument.split(FOLIO_SEPARATOR, 1)[0]


FUND_ISIN_PREFIX = "INF"
"""Indian ISINs for mutual fund units (including exchange-traded funds) start with INF."""


def is_fund(instrument: str) -> bool:
    return isin_of(instrument).startswith(FUND_ISIN_PREFIX)
