"""Golden cases: delivery equity capital gains. Synthetic data; hand computation in each
docstring. Year 2025 = FY 2025-26 (STCG 20%, LTCG 12.5% above ₹1,25,000) unless noted."""

from engine.api import compute_tax_year
from engine.matching.corporate_actions import Bonus, Split
from engine.rules.setoff import LossKind
from tests.golden.helpers import A, B, buy, d, day, lt, sell, st, taxable


def test_g01_simple_stcg() -> None:
    """Buy 100 @1,000 (1-May-25), sell @1,200 (1-Oct-25): STCG 20,000 x 20% = 4,000."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-10-01", 100, 1200)])
    assert taxable(r) == {"STCG @ 20%": d(20000)}
    assert r.special_rate_tax_rounded == d(4000)


def test_g02_ltcg_within_exemption() -> None:
    """Buy 1-Jan-24 @1,000, sell 1-Jun-25 @2,000 x100: LTCG 1,00,000 < 1,25,000 → tax 0."""
    r = compute_tax_year(2025, [buy("2024-01-01", 100, 1000), sell("2025-06-01", 100, 2000)])
    assert r.bucket_nets == {lt("0.125"): d(100000)}
    assert r.setoff.exemption_used == {lt("0.125"): d(100000)}
    assert r.special_rate_tax_rounded == 0


def test_g03_ltcg_above_exemption() -> None:
    """LTCG 2,00,000 - 1,25,000 = 75,000 x 12.5% = 9,375 → rounded to ₹10: 9,380."""
    r = compute_tax_year(2025, [buy("2024-01-01", 100, 1000), sell("2025-06-01", 100, 3000)])
    assert taxable(r) == {"LTCG @ 12.5%": d(75000)}
    assert r.special_rate_tax == d("9375.000")
    assert r.special_rate_tax_rounded == d(9380)


def test_g04_exactly_twelve_months_is_short_term() -> None:
    """Bought 10-Jun-24, sold 10-Jun-25: held not more than 12 months → STCG 10,000 → 2,000."""
    r = compute_tax_year(2025, [buy("2024-06-10", 100, 1000), sell("2025-06-10", 100, 1100)])
    assert taxable(r) == {"STCG @ 20%": d(10000)}
    assert r.capital_gains[0].long_term_after == day("2025-06-10")


def test_g05_one_day_more_is_long_term() -> None:
    """Sold 11-Jun-25 instead: LTCG 10,000, within exemption → tax 0."""
    r = compute_tax_year(2025, [buy("2024-06-10", 100, 1000), sell("2025-06-11", 100, 1100)])
    assert r.bucket_nets == {lt("0.125"): d(10000)}
    assert r.special_rate_tax_rounded == 0


def test_g06_leap_day_clamped_short_term() -> None:
    """Bought 29-Feb-24; 12 months later clamps to 28-Feb-25. Sold 28-Feb-25 (FY 2024-25,
    after 23-Jul-24): STCG 10,000 x 20% = 2,000."""
    r = compute_tax_year(2024, [buy("2024-02-29", 100, 1000), sell("2025-02-28", 100, 1100)])
    assert taxable(r) == {"STCG @ 20%": d(10000)}
    assert r.special_rate_tax_rounded == d(2000)


def test_g07_leap_day_next_day_long_term() -> None:
    """Sold 1-Mar-25: LTCG 10,000 at 12.5%, within exemption → 0."""
    r = compute_tax_year(2024, [buy("2024-02-29", 100, 1000), sell("2025-03-01", 100, 1100)])
    assert r.bucket_nets == {lt("0.125"): d(10000)}
    assert r.special_rate_tax_rounded == 0


def test_g08_charges_deductible_stt_not() -> None:
    """Buy 100 @1,000 + charges 100 (+STT 100); sell @1,200 - charges 120 (+STT 120).
    Gain = 1,20,000 - 120 - (1,00,000 + 100) = 19,780; STT ignored (s.72(3)(b) / s.48).
    Tax 19,780 x 20% = 3,956 → 3,960."""
    r = compute_tax_year(2025, [
        buy("2025-05-01", 100, 1000, charges=100, stt=100),
        sell("2025-10-01", 100, 1200, charges=120, stt=120),
    ])
    assert r.capital_gains[0].gain == d(19780)
    assert r.special_rate_tax_rounded == d(3960)


def test_g09_fifo_split_between_long_and_short() -> None:
    """Lots: 50 @1,000 (1-Mar-24), 50 @1,500 (1-Jan-25). Sell 80 @2,000 on 1-Aug-25.
    FIFO: 50 from lot 1 → LTCG 50,000 (exempt); 30 from lot 2 → STCG 15,000 → tax 3,000."""
    r = compute_tax_year(2025, [
        buy("2024-03-01", 50, 1000), buy("2025-01-01", 50, 1500), sell("2025-08-01", 80, 2000),
    ])
    assert r.bucket_nets == {lt("0.125"): d(50000), st("0.20"): d(15000)}
    assert r.special_rate_tax_rounded == d(3000)
    [left] = r.open_lots
    assert left.quantity == d(20)


def test_g10_grandfathering_fmv_between_cost_and_sale() -> None:
    """1,000 bought 2015 @500; FMV 31-Jan-18 = 800; sold 1-Jun-25 @1,500.
    Cost = max(5,00,000, min(8,00,000, 15,00,000)) = 8,00,000. LTCG 7,00,000.
    Taxable 5,75,000 x 12.5% = 71,875 → 71,880."""
    r = compute_tax_year(2025, [buy("2015-01-01", 1000, 500), sell("2025-06-01", 1000, 1500)],
                         fmv_2018={A: d(800)})
    line = r.capital_gains[0]
    assert (line.cost, line.grandfathered_fmv, line.gain) == (d(800000), d(800000), d(700000))
    assert r.special_rate_tax_rounded == d(71880)


def test_g11_grandfathering_sale_below_fmv() -> None:
    """Cost 500, FMV 800, sale 700: cost = max(5,00,000, min(8,00,000, 7,00,000)) = 7,00,000
    → gain 0 (grandfathering cannot create a loss)."""
    r = compute_tax_year(2025, [buy("2015-01-01", 1000, 500), sell("2025-06-01", 1000, 700)],
                         fmv_2018={A: d(800)})
    assert r.capital_gains[0].gain == 0


def test_g12_grandfathering_cost_above_fmv() -> None:
    """Cost 900, FMV 800, sale 1,000: cost = max(9,00,000, 8,00,000) = 9,00,000 → LTCG
    1,00,000, within exemption."""
    r = compute_tax_year(2025, [buy("2016-01-01", 1000, 900), sell("2025-06-01", 1000, 1000)],
                         fmv_2018={A: d(800)})
    assert r.capital_gains[0].gain == d(100000)
    assert r.special_rate_tax_rounded == 0


def test_g13_grandfathering_after_post_2018_split() -> None:
    """1,000 bought 2015 @500; FMV 800 (pre-split); 1:2 split on 1-Jun-20 → 2,000 shares.
    Sell 2,000 @1,000: FMV value = 800 x 2,000 / 2 = 8,00,000. Cost = max(5,00,000,
    min(8,00,000, 20,00,000)) = 8,00,000. LTCG 12,00,000; taxable 10,75,000 x 12.5% =
    1,34,375 → 1,34,380."""
    r = compute_tax_year(
        2025, [buy("2015-01-01", 1000, 500), sell("2025-06-01", 2000, 1000)],
        actions=[Split(A, day("2020-06-01"), old=1, new=2)], fmv_2018={A: d(800)})
    assert r.capital_gains[0].grandfathered_fmv == d(800000)
    assert r.special_rate_tax_rounded == d(134380)


def test_g14_grandfathering_missing_fmv_warns_and_uses_cost() -> None:
    """No FMV given: actual cost 5,00,000 used, LTCG 10,00,000; taxable 8,75,000 x 12.5% =
    1,09,375 → 1,09,380, with a warning."""
    r = compute_tax_year(2025, [buy("2015-01-01", 1000, 500), sell("2025-06-01", 1000, 1500)])
    assert r.special_rate_tax_rounded == d(109380)
    assert any("31-Jan-2018 FMV" in w for w in r.warnings)


def test_g15_bonus_shares_short_term_and_original_loss_carried() -> None:
    """100 bought 1-Jan-23 @1,000. Bonus 1:1, ex 1-Sep-24, allotted 3-Sep-24 (cost nil).
    Sell 200 @600 on 1-Aug-25. Original 100: LTCG 60,000 - 1,00,000 = -40,000 (LTCL).
    Bonus 100: held from 3-Sep-24 → short-term, STCG 60,000 → tax 12,000.
    LTCL cannot reduce STCG → carried forward 40,000."""
    r = compute_tax_year(
        2025, [buy("2023-01-01", 100, 1000), sell("2025-08-01", 200, 600)],
        actions=[Bonus(A, day("2024-09-01"), held=1, bonus=1, allotment_date=day("2024-09-03"))])
    assert r.bucket_nets == {lt("0.125"): d(-40000), st("0.20"): d(60000)}
    assert r.special_rate_tax_rounded == d(12000)
    [cf] = r.carried_forward
    assert (cf.kind, cf.amount, cf.origin_year) == (LossKind.LONG_TERM_CAPITAL, d(40000), 2025)


def test_g16_split_keeps_cost_and_date() -> None:
    """10 bought 1-Jan-24 @5,000; split 1:5 on 1-Jun-24 → 50. Sell 50 @1,200 on 1-Mar-25
    (FY 2024-25): LTCG 60,000 - 50,000 = 10,000 → exempt. Split warning (Q-002)."""
    r = compute_tax_year(2024, [buy("2024-01-01", 10, 5000), sell("2025-03-01", 50, 1200)],
                         actions=[Split(A, day("2024-06-01"), old=1, new=5)])
    assert r.bucket_nets == {lt("0.125"): d(10000)}
    assert any("Q-002" in w for w in r.warnings)


def test_g17_stcl_against_ltcg() -> None:
    """A: STCL 30,000. B: LTCG 2,00,000. STCL set off against LTCG → 1,70,000;
    exemption → 45,000 x 12.5% = 5,625 → 5,630."""
    r = compute_tax_year(2025, [
        buy("2025-04-10", 100, 1000), sell("2025-06-10", 100, 700),
        buy("2023-01-01", 100, 1000, instrument=B), sell("2025-07-01", 100, 3000, instrument=B),
    ])
    assert taxable(r) == {"LTCG @ 12.5%": d(45000)}
    assert r.special_rate_tax_rounded == d(5630)


def test_g18_ltcl_cannot_reduce_stcg() -> None:
    """A: LTCL 50,000. B: STCG 40,000 → tax 8,000; LTCL 50,000 carried forward."""
    r = compute_tax_year(2025, [
        buy("2023-01-01", 100, 1500), sell("2025-07-01", 100, 1000),
        buy("2025-04-10", 100, 1000, instrument=B), sell("2025-06-10", 100, 1400, instrument=B),
    ])
    assert r.special_rate_tax_rounded == d(8000)
    assert [(e.kind, e.amount) for e in r.carried_forward] == [
        (LossKind.LONG_TERM_CAPITAL, d(50000))]


def test_g19_short_term_gain_and_loss_net() -> None:
    """A: STCG 50,000; B: STCL 20,000 → net STCG 30,000 → 6,000."""
    r = compute_tax_year(2025, [
        buy("2025-04-10", 100, 1000), sell("2025-06-10", 100, 1500),
        buy("2025-04-10", 100, 1000, instrument=B), sell("2025-06-10", 100, 800, instrument=B),
    ])
    assert r.special_rate_tax_rounded == d(6000)


def test_g20_fy2024_25_stcg_rate_cutover() -> None:
    """FY 2024-25. A sold 22-Jul-24 (STCG 10,000 @15% = 1,500); B sold 23-Jul-24
    (STCG 10,000 @20% = 2,000). Total 3,500."""
    r = compute_tax_year(2024, [
        buy("2024-04-05", 100, 1000), sell("2024-07-22", 100, 1100),
        buy("2024-04-05", 100, 1000, instrument=B), sell("2024-07-23", 100, 1100, instrument=B),
    ])
    assert taxable(r) == {"STCG @ 15%": d(10000), "STCG @ 20%": d(10000)}
    assert r.special_rate_tax_rounded == d(3500)


def test_g21_fy2024_25_exemption_applied_to_12_5_first() -> None:
    """FY 2024-25. LTCG 1,00,000 before 23-Jul-24 (10%) and 1,00,000 after (12.5%).
    Exemption 1,25,000: 1,00,000 against 12.5% bucket, 25,000 against 10% bucket (Q-007).
    Tax = 75,000 x 10% = 7,500."""
    r = compute_tax_year(2024, [
        buy("2023-01-01", 100, 1000), sell("2024-07-01", 100, 2000),
        buy("2023-01-01", 100, 1000, instrument=B), sell("2024-08-01", 100, 2000, instrument=B),
    ])
    assert r.setoff.exemption_used == {lt("0.125"): d(100000), lt("0.10"): d(25000)}
    assert r.special_rate_tax_rounded == d(7500)
    assert any("Q-007" in w for w in r.warnings)


def test_g22_fy2024_25_old_rate_loss_against_new_rate_gain() -> None:
    """FY 2024-25. STCL 20,000 (sold before cutover) and STCG 50,000 (after).
    Loss set off against the 20% gain → 30,000 x 20% = 6,000."""
    r = compute_tax_year(2024, [
        buy("2024-04-05", 100, 1000), sell("2024-07-01", 100, 800),
        buy("2024-04-05", 100, 1000, instrument=B), sell("2024-09-01", 100, 1500, instrument=B),
    ])
    assert taxable(r) == {"STCG @ 20%": d(30000)}
    assert r.special_rate_tax_rounded == d(6000)


def test_g23_rounding_ignores_paise_then_nearest_ten() -> None:
    """STCG 6,172.50 x 20% = 1,234.50 → ignore paise 1,234 → nearest ten 1,230."""
    r = compute_tax_year(2025, [buy("2025-05-01", 1, 1000), sell("2025-06-01", 1, "7172.5")])
    assert r.special_rate_tax == d("1234.500")
    assert r.special_rate_tax_rounded == d(1230)


def test_g24_ty2026_27_uses_2025_act() -> None:
    """TY 2026-27 under the Income-tax Act 2025: LTCG 3,00,000 - 1,25,000 = 1,75,000 x 12.5%
    = 21,875 → 21,880. Rate cited to s.198."""
    r = compute_tax_year(2026, [buy("2024-01-01", 100, 1000), sell("2026-06-01", 100, 4000)])
    assert r.pack.label == "TY 2026-27"
    assert r.special_rate_tax_rounded == d(21880)
    sections = {c.section(r.pack.act) for c in r.capital_gains[0].citations}
    assert "s.198" in sections


def test_g25_open_position_not_taxed() -> None:
    """Only a buy: nothing to tax, one open lot."""
    r = compute_tax_year(2025, [buy("2025-05-01", 10, 100)])
    assert r.capital_gains == () and r.special_rate_tax_rounded == 0
    assert len(r.open_lots) == 1
