"""Golden cases: intraday / F&O income, set-off and the carry-forward ledger.
Synthetic data; hand computation in each docstring. Year 2025 = FY 2025-26."""

import pytest

from engine.api import compute_tax_year, compute_tax_years
from engine.rules import UnsupportedTaxYearError
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import A, B, buy, d, day, fut, sell, taxable


def test_g26_intraday_profit_is_speculative_stt_deducted() -> None:
    """Buy 100 @1,000 and sell @1,010 on 2-Jun-25, STT 25 each side. Speculative income =
    1,000 - 50 = 950 (STT deductible for business, s.32(k) / s.36(1)(xv)). No capital gains."""
    r = compute_tax_year(2025, [buy("2025-06-02", 100, 1000, stt=25),
                                sell("2025-06-02", 100, 1010, stt=25)])
    assert r.business.speculative == d(950)
    assert r.setoff.speculative_income == d(950)
    assert r.capital_gains == ()
    assert any("Q-004" in w.message for w in r.warnings)


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
    assert any("Q-010" in w.message for w in r.warnings)


def test_g29_fno_loss_larger_than_everything_carried() -> None:
    """F&O loss 1,00,000; STCG 20,000 absorbed; 80,000 business loss carried forward."""
    r = compute_tax_year(2025, [
        fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 22000),
        buy("2025-04-10", 100, 1000), sell("2025-06-10", 100, 1200),
    ])
    assert r.special_rate_tax_rounded == 0
    assert [(e.kind, e.amount) for e in r.carried_forward] == [(LossKind.BUSINESS, d(80000))]
    assert any("Q-011" in w.message for w in r.warnings)


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
    assert any(w.code == "EXPIRED_LOSS" for w in r.warnings)


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


def test_g40_bf_speculative_used_before_bf_business() -> None:
    """QA case. Intraday profit 10,000; b/f speculative loss of 2021 (its last usable year)
    10,000; b/f business loss of 2024 10,000. Speculative loss goes first and is used up;
    the business loss (8-year life) carries forward."""
    r = compute_tax_year(2025, [buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 1100)],
                         brought_forward=[LossEntry(2024, LossKind.BUSINESS, d(10000)),
                                          LossEntry(2021, LossKind.SPECULATIVE, d(10000))])
    assert r.setoff.speculative_income == 0
    assert r.carried_forward == (LossEntry(2024, LossKind.BUSINESS, d(10000)),)


def test_g41_bf_business_loss_does_not_reduce_capital_gains() -> None:
    """b/f business loss 50,000; STCG 40,000 → tax 8,000; loss carried in full."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 1400)],
                         brought_forward=[LossEntry(2023, LossKind.BUSINESS, d(50000))])
    assert r.special_rate_tax_rounded == d(8000)
    assert r.carried_forward == (LossEntry(2023, LossKind.BUSINESS, d(50000)),)


def test_g42_speculative_loss_not_against_fno_profit() -> None:
    """Intraday loss 5,000; F&O profit 20,000 → business income 20,000, speculative loss
    5,000 carried (s.113 / s.73)."""
    r = compute_tax_year(2025, [
        buy("2025-06-02", 100, 1000), sell("2025-06-02", 100, 950),
        fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 24400),
    ])
    assert r.setoff.business_income == d(20000)
    assert r.carried_forward == (LossEntry(2025, LossKind.SPECULATIVE, d(5000)),)


def test_g43_business_loss_usable_year_8_expired_year_9() -> None:
    """F&O profit 30,000. b/f business loss of 2017 (8th year): used. Of 2016: expired."""
    trades = [fut("BUY", "2025-06-02", 50, 24000), fut("SELL", "2025-06-20", 50, 24600)]
    used = compute_tax_year(2025, trades,
                            brought_forward=[LossEntry(2017, LossKind.BUSINESS, d(10000))])
    assert used.setoff.business_income == d(20000)
    gone = compute_tax_year(2025, trades,
                            brought_forward=[LossEntry(2016, LossKind.BUSINESS, d(10000))])
    assert gone.setoff.business_income == d(30000)


def test_g44_fy2024_25_grandfathering_at_10_percent() -> None:
    """FY 2024-25, sold 1-Jul-24 (before cutover). 1,000 bought 2015 @500, FMV 800, sold
    @1,500: LTCG 7,00,000 - exemption 1,25,000 = 5,75,000 x 10% = 57,500."""
    r = compute_tax_year(2024, [buy("2015-01-01", 1000, 500), sell("2024-07-01", 1000, 1500)],
                         fmv_2018={A: d(800)})
    assert taxable(r) == {"LTCG @ 10%": d(575000)}
    assert r.special_rate_tax_rounded == d(57500)


def test_g45_rounding_last_digit_five_rounds_up() -> None:
    """STCG 6,175 x 20% = 1,235 → 1,240."""
    r = compute_tax_year(2025, [buy("2025-05-01", 1, 1000), sell("2025-06-01", 1, 7175)])
    assert r.special_rate_tax_rounded == d(1240)


def test_g46_setoff_order_and_boundary_flags() -> None:
    """FY 2024-25. C: STCL 20,000 sold before the cutover (15% bucket, no gains there).
    Eligible targets: STCG @20% (B) and LTCG @12.5% (D) → order assumed (Q-008).
    A: bought 10-Oct-23, sold 10-Oct-24, exactly 12 months → boundary flag (Q-013)."""
    c, e = "INE000C01013", "INE000D01014"
    r = compute_tax_year(2024, [
        buy("2024-04-05", 100, 1000, instrument=c), sell("2024-07-01", 100, 800, instrument=c),
        buy("2024-04-05", 100, 1000, instrument=B), sell("2024-09-01", 100, 1500, instrument=B),
        buy("2022-01-01", 100, 1000, instrument=e), sell("2024-09-01", 100, 4000, instrument=e),
        buy("2023-10-10", 10, 100), sell("2024-10-10", 10, 1000),
    ])
    questions = {w.question for w in r.warnings}
    assert {"Q-008", "Q-013"} <= questions
    assert r.setoff.steps[0].against == "STCG @ 20%"


def test_g47_business_lines_carry_citations() -> None:
    r = compute_tax_year(2025, [
        buy("2025-06-02", 10, 100), sell("2025-06-02", 10, 110),
        fut("SELL", "2025-06-02", 50, 100), fut("BUY", "2025-06-05", 50, 90),
    ])
    kinds = sorted((line.speculative, line.income) for line in r.business.lines)
    assert kinds == [(False, d(500)), (True, d(100))]
    assert all(line.citations for line in r.business.lines)


def test_g48_exemption_is_a_setoff_step() -> None:
    r = compute_tax_year(2025, [buy("2024-01-01", 100, 1000), sell("2025-06-01", 100, 2000)])
    [step] = r.setoff.steps
    assert (step.loss, step.amount) == ("LTCG exemption", d(100000))


def test_g49_non_consecutive_years_rejected_and_bad_losses() -> None:
    with pytest.raises(ValueError, match="consecutive"):
        compute_tax_years([2024, 2026], [])
    with pytest.raises(ValueError, match="positive"):
        LossEntry(2024, LossKind.BUSINESS, d(-1))
    with pytest.raises(ValueError, match="not before"):
        compute_tax_year(2025, [], brought_forward=[LossEntry(2025, LossKind.BUSINESS, d(1))])


def test_g50_post_split_grandfathering_flagged() -> None:
    from engine.matching.corporate_actions import Split
    r = compute_tax_year(
        2025, [buy("2015-01-01", 1000, 500), sell("2025-06-01", 3000, 400)],
        actions=[Split(A, day("2020-06-01"), old=1, new=3)], fmv_2018={A: d(800)})
    assert r.capital_gains[0].grandfathered_fmv == d(800000)
    assert any(w.question == "Q-006" for w in r.warnings)
