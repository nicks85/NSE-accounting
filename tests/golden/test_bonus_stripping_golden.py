"""Golden cases: bonus stripping — Income-tax Act 2025 s.175(9),(10); 1961 Act s.94(8).
Synthetic data. Record date = ex-date (T+1) unless stated. Year 2025 = FY 2025-26."""

from engine.api import compute_tax_year
from engine.matching.corporate_actions import Bonus
from tests.golden.helpers import A, B, buy, d, day, sell

BONUS = Bonus(A, day("2025-06-15"), held=1, bonus=1, allotment_date=day("2025-06-17"))
OTHER_GAIN = [buy("2025-04-10", 100, 1000, instrument=B),
              sell("2025-08-10", 100, 1500, instrument=B)]  # STCG 50,000


def test_g51_loss_stripped_and_moved_to_bonus_cost() -> None:
    """Buy 100 @1,000 on 1-May-25 (within 3 months before record date 15-Jun-25). 1:1 bonus.
    Sell the 100 originals @500 on 1-Jul-25 (within 9 months), keep the 100 bonus shares.
    Loss 50,000 is ignored; bonus lot cost becomes 50,000. B's STCG 50,000 stays taxable:
    50,000 x 20% = 10,000."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 500),
                                *OTHER_GAIN], actions=[BONUS])
    stripped = next(line for line in r.capital_gains if line.disposal.instrument == A)
    assert stripped.disposal.stripped_loss == d(50000) and stripped.gain == 0
    assert r.special_rate_tax_rounded == d(10000)
    [bonus_lot] = r.open_lots
    assert bonus_lot.cost == d(50000)
    assert any("bonus stripping" in w.message for w in r.warnings)


def test_g52_later_sale_of_bonus_shares_uses_stripped_cost() -> None:
    """As g51, then sell the 100 bonus shares @700 on 1-Sep-25: STCG = 70,000 - 50,000 =
    20,000 (not 70,000). Total STCG with B: 70,000 x 20% = 14,000."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 500),
                                sell("2025-09-01", 100, 700), *OTHER_GAIN], actions=[BONUS])
    bonus_line = next(line for line in r.capital_gains
                      if line.disposal.open_trade_id.startswith("BONUS"))
    assert bonus_line.gain == d(20000)
    assert r.special_rate_tax_rounded == d(14000)


def test_g53_bought_more_than_three_months_before_not_stripped() -> None:
    """Bought 1-Mar-25 (window starts 15-Mar-25): loss 50,000 allowed; it absorbs B's STCG
    50,000 → tax 0."""
    r = compute_tax_year(2025, [buy("2025-03-01", 100, 1000), sell("2025-07-01", 100, 500),
                                *OTHER_GAIN], actions=[BONUS])
    assert all(line.disposal.stripped_loss == 0 for line in r.capital_gains)
    assert r.special_rate_tax_rounded == 0


def test_g54_sold_more_than_nine_months_after_not_stripped() -> None:
    """Window ends 15-Mar-26; sold 20-Mar-26 (still FY 2025-26): loss allowed → tax 0."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2026-03-20", 100, 500),
                                *OTHER_GAIN], actions=[BONUS])
    assert r.special_rate_tax_rounded == 0


def test_g55_no_bonus_shares_left_not_stripped() -> None:
    """Sell all 200 (originals + bonus) @500 on 1-Jul-25: no bonus shares held after the sale,
    so the 50,000 loss is allowed and nets against the bonus shares' 50,000 gain."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 200, 500)],
                         actions=[BONUS])
    assert all(line.disposal.stripped_loss == 0 for line in r.capital_gains)
    assert sum(line.gain for line in r.capital_gains) == 0


def test_g56_gain_on_originals_not_affected() -> None:
    """Originals sold at a gain (@1,200): the rule only ignores losses."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 1200)],
                         actions=[BONUS])
    assert r.capital_gains[0].gain == d(20000)


def test_g57_explicit_record_date() -> None:
    """Record date 20-Jun-25 given explicitly, so the window starts 20-Mar-25.
    Bought 19-Mar-25: outside → loss allowed. Bought 21-Mar-25: inside → loss stripped."""
    bonus = Bonus(A, day("2025-06-20"), held=1, bonus=1, record_date=day("2025-06-20"))
    early = compute_tax_year(2025, [buy("2025-03-19", 100, 1000), sell("2025-07-01", 100, 500)],
                             actions=[bonus])
    late = compute_tax_year(2025, [buy("2025-03-21", 100, 1000), sell("2025-07-01", 100, 500)],
                            actions=[bonus])
    assert early.capital_gains[0].disposal.stripped_loss == 0
    assert late.capital_gains[0].disposal.stripped_loss == d(50000)


def test_g58_unverified_flag_only_for_1961_years() -> None:
    """FY 2024-25 (1961 Act): flagged Q-005. TY 2026-27 (2025 Act, verified): not flagged."""
    old = compute_tax_year(
        2024, [buy("2024-05-01", 100, 1000), sell("2024-07-01", 100, 500)],
        actions=[Bonus(A, day("2024-06-15"), held=1, bonus=1)])
    new = compute_tax_year(
        2026, [buy("2026-05-01", 100, 1000), sell("2026-07-01", 100, 500)],
        actions=[Bonus(A, day("2026-06-15"), held=1, bonus=1)])
    assert old.capital_gains[0].disposal.stripped_loss == d(50000)
    assert any(w.question == "Q-005" for w in old.warnings)
    assert not any(w.question == "Q-005" for w in new.warnings)
