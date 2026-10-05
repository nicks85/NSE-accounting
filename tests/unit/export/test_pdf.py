from decimal import Decimal

import pypdfium2 as pdfium
import pytest

from engine.api import FundClass, compute_tax_year
from engine.export.pdf import inr, render_summary
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import HYBRID_FUND, A, buy, d, fut, mf, sell


def _text(pdf_bytes: bytes) -> str:
    document = pdfium.PdfDocument(pdf_bytes)
    return "\n".join(document[i].get_textpage().get_text_range() for i in range(len(document)))


def _report():  # type: ignore[no-untyped-def]
    return compute_tax_year(2025, [
        buy("2015-01-01", 1000, 500), sell("2025-06-01", 1000, 1500),
        buy("2025-04-10", 100, 1000, instrument="INE000C01013"),
        sell("2025-12-20", 100, 1300, instrument="INE000C01013"),
        mf("BUY", "2022-01-01", 1000, 100, HYBRID_FUND),
        mf("SELL", "2025-09-01", 1000, 150, HYBRID_FUND),
        buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 1010),
        fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 23000),
    ], fmv_2018={A: d(800)}, fund_classes={HYBRID_FUND: FundClass.OTHER},
        brought_forward=[LossEntry(2016, LossKind.SHORT_TERM_CAPITAL, d(5))])


@pytest.mark.parametrize(("amount", "text"), [
    ("1234567.891", "Rs. 12,34,567.89"), ("-5", "-Rs. 5.00"), ("999", "Rs. 999.00"),
    ("100000", "Rs. 1,00,000.00"), ("0.005", "Rs. 0.01"),
    ("123456789012", "Rs. 1,23,45,67,89,012.00"),
])
def test_indian_grouping(amount: str, text: str) -> None:
    assert inr(Decimal(amount)) == text


def test_summary_contents() -> None:
    """Figures from the report: taxable 5,75,000 + 31,000 at 12.5% = 75,750."""
    pdf = render_summary(_report(), names={A: "SYNTHETIC A LTD"})
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    for expected in ("tax summary for FY 2025-26", "Income-tax Act, 1961", "not tax",
                     "Rs. 75,750.00", "Rs. 1,25,000.00", "SYNTHETIC A LTD (INE000A01011)",
                     "Current-year F&O loss", "s.71", "expired, not usable", "Non-speculative",
                     "UNVERIFIED", "Q-012"):
        assert expected in text, expected
    assert "F&O;" not in text and "■" not in text and "₹" not in text


def test_reproducible_bytes() -> None:
    report = _report()
    assert render_summary(report) == render_summary(report)


def test_empty_year_and_manual_line() -> None:
    assert "Summary" in _text(render_summary(compute_tax_year(2025, [])))
    manual = compute_tax_year(2024, [mf("BUY", "2020-01-01", 1000, 10, HYBRID_FUND),
                                     mf("SELL", "2024-07-01", 1000, 20, HYBRID_FUND)],
                              fund_classes={HYBRID_FUND: FundClass.OTHER})
    text = _text(render_summary(manual))
    assert "MANUAL" in text and "Q-021" in text


def test_long_reports_paginate_with_carried_losses() -> None:
    trades = []
    for i in range(150):
        instrument = f"INE{i:03d}A01011"
        trades += [buy("2025-04-10", 1, 100, instrument=instrument),
                   sell("2025-06-10", 1, 90, instrument=instrument)]
    report = compute_tax_year(2025, trades)
    document = pdfium.PdfDocument(render_summary(report))
    assert len(document) > 3
    assert "carried forward" in _text(render_summary(report))
