"""Rounding of tax payable: ignore paise, then round to the nearest multiple of ₹10, rounding
up when the last digit is 5 or more. Income-tax Act 2025 s.516; 1961 Act s.288A/s.288B.
Source: docs/sources/income-tax-act-2025-as-amended-by-fa-2026.pdf."""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


def round_to_ten(amount: Decimal) -> Decimal:
    rupees = amount.quantize(Decimal(1), rounding=ROUND_DOWN)
    return (rupees / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 10


def round_rupee(amount: Decimal) -> Decimal:
    """Whole rupees for display of line items (ITR schedules take whole rupees)."""
    return amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)
