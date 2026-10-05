from decimal import Decimal

import pypdfium2 as pdfium
import pytest

from engine.api import FundClass, compute_tax_year
from engine.export.pdf import inr, render_summary
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import HYBRID_FUND, A, buy, d, fut, mf, sell


def _text(pdf_bytes: bytes) -> str:
    """All page text with whitespace collapsed (wrapped cells break lines)."""
    document = pdfium.PdfDocument(pdf_bytes)
    raw = " ".join(document[i].get_textpage().get_text_range() for i in range(len(document)))
    return " ".join(raw.split())


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
    ("123456789012", "Rs. 1,23,45,67,89,012.00"), ("-0.004", "Rs. 0.00"), ("-0", "Rs. 0.00"),
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
    assert "&amp;" not in text and "■" not in text and "₹" not in text


def test_reproducible_bytes_across_processes() -> None:
    import hashlib
    import subprocess
    import sys
    from pathlib import Path

    code = ("import hashlib\n"
            "from tests.unit.export.test_pdf import _report\n"
            "from engine.export.pdf import render_summary\n"
            "print(hashlib.sha256(render_summary(_report())).hexdigest())\n")
    root = Path(__file__).resolve().parents[3]
    digests = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,  # noqa: S603
                       check=True, cwd=root, env={"PYTHONHASHSEED": seed, "TZ": tz,
                                                  "PATH": ""}).stdout.strip()
        for seed, tz in (("1", "UTC"), ("2", "Asia/Kolkata"))
    }
    assert digests == {hashlib.sha256(render_summary(_report())).hexdigest()}


def test_empty_year_and_manual_line() -> None:
    assert "Summary" in _text(render_summary(compute_tax_year(2025, [])))
    manual = compute_tax_year(2024, [mf("BUY", "2020-01-01", 1000, 10, HYBRID_FUND),
                                     mf("SELL", "2024-07-01", 1000, 20, HYBRID_FUND)],
                              fund_classes={HYBRID_FUND: FundClass.OTHER})
    text = _text(render_summary(manual))
    assert "MANUAL" in text and "Q-021" in text
    assert "not in any total above" in text
    assert "Rs. 10,000.00" not in text  # the uncomputed gain isn't shown as a figure


def test_tax_year_under_2025_act() -> None:
    report = compute_tax_year(2026, [buy("2024-01-01", 100, 1000), sell("2026-06-01", 100, 4000)],
                              brought_forward=[LossEntry(2025, LossKind.SPECULATIVE, d(10))])
    text = _text(render_summary(report))
    assert "TY 2026-27" in text and "Income-tax Act, 2025" in text and "s.198" in text
    assert "FY 2025-26" in text  # the loss arose under the 1961 Act


def test_hostile_names_are_escaped() -> None:
    report = compute_tax_year(2025, [buy("2025-05-01", 1, 1), sell("2025-06-01", 1, 2)])
    text = _text(render_summary(report, names={A: "A<b>&B ₹ 東"}))
    assert "A<b>&B Rs. ?" in text


def test_remote_image_is_never_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a Paragraph with a remote <img> must not connect (trustedHosts pinned None)."""
    import io
    import socket

    from reportlab.platypus import Paragraph, SimpleDocTemplate

    def trip(*_: object, **__: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "getaddrinfo", trip)
    monkeypatch.setattr(socket, "create_connection", trip)
    import engine.export.pdf  # noqa: F401 - pins rl_config.trustedHosts

    with pytest.raises(OSError, match="Cannot open resource") as caught:
        story = [Paragraph('<img src="http://example.invalid/x.png" width="10" height="10"/>')]
        SimpleDocTemplate(io.BytesIO()).build(story)
    assert "network used" not in str(caught.value)


def test_long_reports_paginate_with_carried_losses() -> None:
    trades = []
    for i in range(150):
        instrument = f"INE{i:03d}A01011"
        trades += [buy("2025-04-10", 1, 100, instrument=instrument),
                   sell("2025-06-10", 1, 90, instrument=instrument)]
    pdf = render_summary(compute_tax_year(2025, trades))
    assert len(pdfium.PdfDocument(pdf)) > 3
    assert "carried forward" in _text(pdf)
