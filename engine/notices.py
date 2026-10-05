"""Structured warnings the UI can link to report lines."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Notice:
    code: str
    """UNVERIFIED, MISSING_FMV, BONUS_STRIPPING, EXPIRED_LOSS, SCOPE or ENGINE."""
    message: str
    question: str | None = None
    """docs/OPEN_QUESTIONS.md id for UNVERIFIED notices."""
    ref: str | None = None
    """Trade id the notice is about, when there is one."""
