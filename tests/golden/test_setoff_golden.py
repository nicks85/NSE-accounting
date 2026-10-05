"""Golden cases: intraday / F&O income, set-off and the carry-forward ledger.
Synthetic data; hand computation in each docstring. Year 2025 = FY 2025-26."""

import pytest

from engine.api import compute_tax_year, compute_tax_years
from engine.rules import UnsupportedTaxYearError
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import B, buy, d, fut, sell


def test_g26_intraday_profit_is_speculative_stt_deducted() -> None:
    """Buy 100 @1,000 and sell @1,010 on 2-Jun-25, STT 25 each side. Speculative income =
    1,000 - 50 = 950 (STT deductible for business, s.32(k) / s.36(1)(xv)). No capital gains."""
    r = compute_tax_year(2025, [buy("2025-06-02", 100, 1000, stt=25),
                                sell("2025-06-02", 100, 1010, stt=25)])
    assert r.business.speculative == d(950)
    assert r.setoff.speculative_income == d(950)
    assert r.capital_gains == ()
    assert any("Q-004" in w for w in r.warnings)


def test_g27_intraday_loss_carried_four_years_not_against_cg() -> None:
    """Intraday loss 5,000; STCG 10,000 on B → CG tax 2,000; speculative loss carried."""
    r = compute_tax_year(2025, [
        buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 950),
        buy("2025-05-01", 100, 1000, instrument=B), sell("2025-07-01", 100, 1100, instrument=B),
    ])
    assert r.special_rate_tax_rounded == d(2000)
    assert [(e.kind, e.amount) for e in r.carried_forward] == [(LossKind.SPECULATIVE, d(5000))]


def test_g28_fno_loss_against_intraday_then_capital_gains() -> None:
    """F&O loss 30,000 (50 @24,000 → 23,400). Intraday profit 10,000. STCG 50,000 on B.
    Step 2: 10,000 against speculative income. Step 3 (Q-010): 20,000 against STCG →
    30,000 x 20% = 6,000. Nothing carried forward."""
    r = compute_tax_year(2025, [
        fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 23400),
        buy("2025-06-03", 100, 1000), sell("2025-06-03", 100, 1100),
        buy("2025-04-10", 100, 1000, instrument=B), sell("2025-06-10", 100, 1500, instrument=B),
    ])
    assert r.business.non_speculative == d(-30000)
    assert r.setoff.speculative_income == 0
    assert r.special_rate_tax_rounded == d(6000)
    assert r.carried_forward == ()
    assert any("Q-010" in w for w in r.warnings)


def test_g29_fno_loss_larger_than_everything_carried() -> None:
    """F&O loss 1,00,000; STCG 20,000 absorbed; 80,000 business loss carried forward."""
    r = compute_tax_year(2025, [
        fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 22000),
        buy("2025-04-10", 100, 1000), sell("2025-06-10", 100, 1200),
    ])
    assert r.special_rate_tax_rounded == 0
    assert [(e.kind, e.amount) for e in r.carried_forward] == [(LossKind.BUSINESS, d(80000))]
    assert any("Q-011" in w for w in r.warnings)


def test_g30_fno_profit_with_brought_forward_business_loss() -> None:
    """F&O profit 50,000; b/f business loss of 2022 = 30,000 → business income 20,000."""
    r = compute_tax_year(
        2025, [fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 25000)],
        brought_forward=[LossEntry(2022, LossKind.BUSINESS, d(30000))])
    assert r.setoff.business_income == d(20000)
    assert r.carried_forward == ()


def test_g31_fno_short_stt_deducted() -> None:
    """Sell 50 @100 (STT 10), buy back @80: gain 1,000 - STT 10 = 990 business income."""
    r = compute_tax_year(2025, [fut("SELL", "2025-06-02", 50, 100, stt=10),
                                fut("BUY", "2025-06-05", 50, 80)])
    assert r.setoff.business_income == d(990)


def test_g32_brought_forward_speculative_partly_used() -> None:
    """b/f speculative loss of 2023 = 8,000; intraday profit 5,000 → income 0, 3,000 carried
    with its original year."""
    r = compute_tax_year(2025, [buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 1050)],
                         brought_forward=[LossEntry(2023, LossKind.SPECULATIVE, d(8000))])
    assert r.setoff.speculative_income == 0
    assert r.carried_forward == (LossEntry(2023, LossKind.SPECULATIVE, d(3000)),)


def test_g33_speculative_loss_expires_after_four_years() -> None:
    """b/f speculative loss of 2020 in 2025: 5 years > 4 → expired, not used."""
    r = compute_tax_year(2025, [buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 1050)],
                         brought_forward=[LossEntry(2020, LossKind.SPECULATIVE, d(8000))])
    assert r.setoff.speculative_income == d(5000)
    assert r.setoff.expired == (LossEntry(2020, LossKind.SPECULATIVE, d(8000)),)
    assert any("expired" in w for w in r.warnings)


def test_g34_capital_loss_usable_in_eighth_year() -> None:
    """b/f STCL of 2017 in 2025 (8 years): set off against LTCG 2,00,000 → 1,90,000;
    exemption → 65,000 x 12.5% = 8,125 → 8,130."""
    r = compute_tax_year(2025, [buy("2023-01-01", 100, 1000), sell("2025-07-01", 100, 3000)],
                         brought_forward=[LossEntry(2017, LossKind.SHORT_TERM_CAPITAL, d(10000))])
    assert r.special_rate_tax_rounded == d(8130)


def test_g35_capital_loss_expired_in_ninth_year() -> None:
    """b/f STCL of 2016 in 2025 (9 years): expired. LTCG 2,00,000 → tax 9,380."""
    r = compute_tax_year(2025, [buy("2023-01-01", 100, 1000), sell("2025-07-01", 100, 3000)],
                         brought_forward=[LossEntry(2016, LossKind.SHORT_TERM_CAPITAL, d(10000))])
    assert r.special_rate_tax_rounded == d(9380)
    assert len(r.setoff.expired) == 1


def test_g36_brought_forward_ltcl_not_against_stcg() -> None:
    """b/f LTCL of 2024 = 40,000; STCG 40,000 → tax 8,000; LTCL carried unchanged."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 1400)],
                         brought_forward=[LossEntry(2024, LossKind.LONG_TERM_CAPITAL, d(40000))])
    assert r.special_rate_tax_rounded == d(8000)
    assert r.carried_forward == (LossEntry(2024, LossKind.LONG_TERM_CAPITAL, d(40000)),)


def test_g37_brought_forward_business_loss_against_speculative() -> None:
    """b/f business loss of 2023 = 10,000; no F&O this year; intraday profit 6,000 →
    speculative income 0 (s.112 / s.72 allows any business income); 4,000 carried."""
    r = compute_tax_year(2025, [buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 1060)],
                         brought_forward=[LossEntry(2023, LossKind.BUSINESS, d(10000))])
    assert r.setoff.speculative_income == 0
    assert r.carried_forward == (LossEntry(2023, LossKind.BUSINESS, d(4000)),)


def test_g38_multi_year_chain_carries_stcl() -> None:
    """FY 2024-25: STCL 50,000 (sold 1-Dec-24). FY 2025-26: STCG 80,000; b/f STCL 50,000 →
    30,000 x 20% = 6,000."""
    first, second = compute_tax_years([2024, 2025], [
        buy("2024-05-01", 100, 1000), sell("2024-12-01", 100, 500),
        buy("2025-05-01", 100, 1000), sell("2025-09-01", 100, 1800),
    ])
    assert first.carried_forward == (LossEntry(2024, LossKind.SHORT_TERM_CAPITAL, d(50000)),)
    assert second.special_rate_tax_rounded == d(6000)
    assert second.carried_forward == ()


def test_g39_unsupported_year() -> None:
    """FY 2023-24 has no rule pack yet."""
    with pytest.raises(UnsupportedTaxYearError, match="FY 2024-25"):
        compute_tax_year(2023, [])
