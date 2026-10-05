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
    assert any(w.question == "Q-014" for w in r.warnings)


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


def test_g57_window_edges_inclusive() -> None:
    """Record 15-Jun-25. Bought 15-Mar-25 (first day of the window) → stripped.
    Sold 15-Mar-26 (last day of the 9-month window) → stripped."""
    start = compute_tax_year(2025, [buy("2025-03-15", 100, 1000), sell("2025-07-01", 100, 500)],
                             actions=[BONUS])
    end = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2026-03-15", 100, 500)],
                           actions=[BONUS])
    assert start.capital_gains[0].disposal.stripped_loss == d(50000)
    assert end.capital_gains[0].disposal.stripped_loss == d(50000)


def test_g59_record_date_after_ex_date_and_buy_on_ex_date() -> None:
    """QA case. Ex 15-Jun, record 16-Jun, allotted 18-Jun. 100 bought 1-May (entitled) and
    100 bought 15-Jun (ex-date: not entitled to the bonus). Sell 200 @500 on 1-Jul.
    Only the 1-May lot's 50,000 loss is ignored; the 15-Jun lot's loss is allowed."""
    bonus = Bonus(A, day("2025-06-15"), held=1, bonus=1, record_date=day("2025-06-16"),
                  allotment_date=day("2025-06-18"))
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), buy("2025-06-15", 100, 1000),
                                sell("2025-07-01", 200, 500)], actions=[bonus])
    stripped = [line.disposal.stripped_loss for line in r.capital_gains]
    assert stripped == [d(50000), d(0)]
    assert r.open_lots[0].cost == d(50000)


def test_g60_sale_before_allotment_not_stripped() -> None:
    """Sold 16-Jun (after record 15-Jun, before allotment 17-Jun): bonus shares not yet held
    on the date of sale → loss allowed (best guess, Q-014)."""
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-06-16", 100, 500)],
                         actions=[BONUS])
    assert r.capital_gains[0].disposal.stripped_loss == 0


def test_g61_bonus_partly_sold_earlier_loss_goes_to_remaining() -> None:
    """Hold 50 old (2024) + 100 bought 1-May-25; 1:1 bonus → 150 bonus shares.
    Sell 200 @500 on 1-Jul-25, FIFO: 50 old shares (long-term loss 25,000, allowed: bought
    outside the window), 100 window shares (loss 50,000 ignored), 50 bonus shares (gain
    25,000). The remaining 100 bonus shares take the 50,000 as cost."""
    r = compute_tax_year(2025, [buy("2024-01-01", 50, 1000), buy("2025-05-01", 100, 1000),
                                sell("2025-07-01", 200, 500)], actions=[BONUS])
    assert [line.disposal.stripped_loss for line in r.capital_gains] == [0, d(50000), 0]
    [rest] = r.open_lots
    assert (rest.quantity, rest.cost) == (d(100), d(50000))


def test_g62_split_between_record_date_and_sale() -> None:
    """1:2 split on 20-Jun after the bonus: 100 window shares become 200. Selling them at a
    loss on 1-Jul is still stripped; remaining 200 bonus shares carry the loss as cost."""
    from engine.matching.corporate_actions import Split
    r = compute_tax_year(2025, [buy("2025-05-01", 100, 1000), sell("2025-07-01", 200, 250)],
                         actions=[BONUS, Split(A, day("2025-06-20"), old=1, new=2)])
    assert r.capital_gains[0].disposal.stripped_loss == d(50000)
    [rest] = r.open_lots
    assert (rest.quantity, rest.cost) == (d(200), d(50000))


def test_g63_opening_bonus_lot_warns() -> None:
    """Bonus shares supplied as an opening lot can't be checked for stripping: warning."""
    from engine.models import Lot
    lot = Lot(A, day("2025-06-17"), d(100), d(0), d(0), d(0), "BONUS:INE000A01011:2025-06-15")
    r = compute_tax_year(2025, [], opening_lots=[lot])
    assert any("Q-014" in w.message for w in r.warnings)


def test_g64_stripping_notice_only_in_year_of_sale() -> None:
    """Stripped in FY 2025-26; the FY 2026-27 report must not repeat the notice."""
    from engine.api import compute_tax_years
    first, second = compute_tax_years(
        [2025, 2026], [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 500)],
        actions=[BONUS])
    assert any(w.code == "BONUS_STRIPPING" for w in first.warnings)
    assert not any(w.code == "BONUS_STRIPPING" for w in second.warnings)


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
