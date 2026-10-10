"""Golden cases: residential status per tax year (brief 0006, Q-028). Synthetic data.
The status changes notices, never the figure: Kosh applies neither the basic-exemption
shortfall (resident only, 2025 Act s.196(2), s.198(3)) nor the rebate (s.156)."""

from pathlib import Path

import pytest

from engine.api import compute_tax_year, compute_tax_years
from engine.export.pdf import render_summary
from engine.ledger import Batch, Ledger
from engine.ledger.settings import Settings
from tests.golden.helpers import buy, d, sell

TRADES = [buy("2024-05-01", 100, 1000), sell("2025-07-01", 100, 2600)]
"""LTCG 1,60,000 - 1,25,000 = 35,000 x 12.5% = 4,375 → 4,380, whatever the status."""


@pytest.mark.parametrize("status", ["RES", "NOR", "NRI"])
def test_r1_the_figure_is_the_same_for_every_status(status: str) -> None:
    r = compute_tax_year(2025, TRADES, residency=status)
    assert r.special_rate_tax_rounded == d(4380) and r.residency == status
    [note] = [n for n in r.warnings if n.code == "RESIDENCY"]
    assert note.question == "Q-028"


def test_r2_what_each_status_says() -> None:
    def note(status: str) -> str:
        r = compute_tax_year(2025, TRADES, residency=status)
        return next(n.message for n in r.warnings if n.code == "RESIDENCY")

    assert "actual tax may be lower" in note("RES") and "s.196(2)" in note("RES")
    assert note("NOR").startswith("Resident but not ordinarily resident")
    assert "don't apply" in note("NRI") and "Form 26AS" in note("NRI")
    assert "DTAA" in note("NRI") and "s.198(2)" in note("NRI")
    for status in ("RES", "NOR", "NRI"):  # all cited from the Act text now (QA, brief 0006)
        assert not any(c.question == "Q-028"
                       for c in compute_tax_year(2025, TRADES, residency=status).unverified)
    assert "1961 Act s.87A" in note("NRI") and "s.393(2)" in note("NRI")


def test_r3_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="residency must be"):
        compute_tax_year(2025, TRADES, residency="XYZ")


def test_r4_a_status_per_year_over_several_years() -> None:
    first, second = compute_tax_years([2024, 2025], TRADES, residency={2025: "NRI"})
    assert (first.residency, second.residency) == ("RES", "NRI")


def test_r5_saved_per_person_and_year_and_shown_in_the_pdf(tmp_path: Path) -> None:
    import pypdfium2 as pdfium

    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        person = ledger.ensure_profile("Synthetic").id
        ledger.import_trades(person, Batch(file_sha256="a"), TRADES)
        ledger.save_settings(person, Settings(residency={2025: "NRI"}, filed_on_time={2025: True}))
        assert dict(ledger.settings(person).residency) == {2025: "NRI"}
        [report] = ledger.compute(person, [2025])
        assert report.residency == "NRI"
        ledger.save_settings(person, Settings())
        assert dict(ledger.settings(person).residency) == {}
    page = pdfium.PdfDocument(render_summary(report))[0].get_textpage().get_text_range()
    assert "Residential status for this year: Non-resident" in " ".join(page.split())
    with pytest.raises(ValueError, match="residency must be"):
        Settings(residency={2025: "abroad"})
