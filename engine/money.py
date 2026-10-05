"""Decimal helpers. Money is always ``Decimal``; floats are rejected (CLAUDE.md rule 2)."""

from decimal import ROUND_HALF_EVEN, Decimal

ZERO = Decimal(0)
INTERNAL_SCALE = Decimal("1e-10")
"""Fixed scale for apportioned shares. Keeping every split amount at this scale makes later
additions and subtractions exact (no silent loss at the 28-digit context limit), so split
pieces always sum back to the original. Final rounding happens only at reporting."""


def require_decimal(name: str, value: object) -> Decimal:
    """Return ``value`` if it is a finite ``Decimal``, else raise ``TypeError``/``ValueError``."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def apportion(total: Decimal, part: Decimal, whole: Decimal) -> Decimal:
    """Share of ``total`` attributable to ``part`` out of ``whole``.

    Callers that split an amount repeatedly must subtract each share from the running total
    and give the final piece the remainder, so pieces always sum exactly to the original.
    """
    if part == whole:
        return total
    return (total * part / whole).quantize(INTERNAL_SCALE, rounding=ROUND_HALF_EVEN)
