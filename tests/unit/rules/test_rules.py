from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from engine.dates import add_months, tax_year_bounds, tax_year_of
from engine.rules import PACKS, pack_for_year
from engine.rules.base import Act, Citation
from engine.rules.rounding import round_rupee, round_to_ten


def test_tax_year_helpers() -> None:
    assert tax_year_of(date(2025, 3, 31)) == 2024
    assert tax_year_of(date(2025, 4, 1)) == 2025
    assert tax_year_bounds(2025) == (date(2025, 4, 1), date(2026, 3, 31))
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2024, 11, 15), 3) == date(2025, 2, 15)


def test_fy2024_25_rates_change_on_23_july() -> None:
    pack = pack_for_year(2024)
    assert pack.rates_on(date(2024, 7, 22)).stcg_equity == Decimal("0.15")
    assert pack.rates_on(date(2024, 7, 23)).ltcg_equity == Decimal("0.125")
    with pytest.raises(ValueError, match="no rates"):
        pack.rates_on(date(2024, 3, 31))


def test_every_pack_has_act_and_exemption() -> None:
    assert [p.act for p in PACKS.values()] == [Act.ACT_1961, Act.ACT_1961, Act.ACT_2025]
    assert all(p.ltcg_exemption == Decimal(125000) for p in PACKS.values())


def test_every_unverified_citation_names_an_open_question() -> None:
    open_questions = (Path(__file__).parents[3] / "docs/OPEN_QUESTIONS.md").read_text()
    for pack in PACKS.values():
        for citation in pack.citations.values():
            assert citation.url.startswith("https://")
            if citation.unverified:
                assert citation.question and f"## {citation.question}" in open_questions


def test_citation_section_falls_back_to_other_act() -> None:
    both = Citation("t", "s.1", "s.2", "https://x")
    assert both.section(Act.ACT_1961) == "s.1" and both.section(Act.ACT_2025) == "s.2"
    assert Citation("t", "s.80", None, "https://x").section(Act.ACT_2025) == "s.80"
    assert Citation("t", None, None, "https://x").section(Act.ACT_1961) == "—"


def test_rounding() -> None:
    assert round_to_ten(Decimal("12344.99")) == 12340
    assert round_to_ten(Decimal("12345")) == 12350
    assert round_to_ten(Decimal("12349.99")) == 12350
    assert round_rupee(Decimal("10.5")) == 11
