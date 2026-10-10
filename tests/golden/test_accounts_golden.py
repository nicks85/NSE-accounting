"""Golden cases: FIFO per demat account and transfers between one's own accounts (brief 0003;
CBDT Circular 768, Q-026). Synthetic data; hand computation in each docstring.
Z = "Zerodha", G = "Groww". Year 2025 = FY 2025-26."""

from dataclasses import replace
from datetime import date

from engine.api import compute_tax_year
from engine.matching.corporate_actions import Bonus
from engine.models import Trade, Transfer
from tests.golden.helpers import A, buy, d, sell

Z, G = "Zerodha", "Groww"


def at(account: str, trade: Trade) -> Trade:
    return replace(trade, account=account)


def lines(report: object) -> list[tuple[str, date, object, str, object]]:
    return [(line.disposal.account, line.disposal.acquired_on, line.disposal.quantity,
             line.bucket.label, line.gain) for line in report.capital_gains]  # type: ignore[attr-defined]


def test_a1_a_sale_uses_only_its_own_accounts_purchases() -> None:
    """Z bought 100 @100 on 2-Jan-24; G bought 100 @200 on 3-Jun-24; G sells 100 @250 on
    1-Jul-25. Per account the sale uses G's lot: held over 12 months, LTCG 100 x 50 = 5,000
    (across accounts it would have used Z's older lot: 15,000). Z's 100 stay held."""
    r = compute_tax_year(2025, [at(Z, buy("2024-01-02", 100, 100)),
                                at(G, buy("2024-06-03", 100, 200)),
                                at(G, sell("2025-07-01", 100, 250))])
    assert lines(r) == [(G, date(2024, 6, 3), d(100), "LTCG @ 12.5%", d(5000))]
    assert [(lot.account, lot.quantity) for lot in r.open_lots] == [(Z, d(100))]
    assert any(n.question == "Q-026" for n in r.warnings)  # FIFO_PER_ACCOUNT is UNVERIFIED
    assert any(c.question == "Q-026" for c in r.capital_gains[0].citations)


def test_a2_shares_in_another_account_dont_cover_a_sale() -> None:
    """G sells 100 it never bought; Z holds 100. The sale is missing purchase history in G."""
    r = compute_tax_year(2025, [at(Z, buy("2024-01-02", 100, 100)),
                                at(G, sell("2025-07-01", 100, 250, trade_id="S"))])
    [gap] = r.missing_history
    assert (gap.trade_id, gap.account, gap.quantity) == ("S", G, d(100))
    assert r.capital_gains == ()


def test_a3_moved_shares_queue_by_entry_but_keep_their_purchase_date() -> None:
    """Z bought 100 @100 on 2-Jan-23. G bought 50 @300 on 3-Jun-24. 100 moved Z→G on
    2-Sep-24. G sells 120 @400 on 2-Jun-25. FIFO by entry into G (Circular 768): first G's own
    50 (entered 3-Jun-24): held under 12 months, STCG 50 x 100 = 5,000; then 70 of the moved
    lot (entered 2-Sep-24, bought 2-Jan-23): LTCG 70 x 300 = 21,000. 30 moved shares stay."""
    move = Transfer("T1", date(2024, 9, 2), A, d(100), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2023-01-02", 100, 100)),
                                at(G, buy("2024-06-03", 50, 300)),
                                at(G, sell("2025-06-02", 120, 400))], transfers=[move])
    assert lines(r) == [(G, date(2024, 6, 3), d(50), "STCG @ 20%", d(5000)),
                        (G, date(2023, 1, 2), d(70), "LTCG @ 12.5%", d(21000))]
    [left] = r.open_lots
    assert (left.account, left.quantity, left.acquired_on, left.entered_on) == (
        G, d(30), date(2023, 1, 2), date(2024, 9, 2))
    assert left.cost == d(3000)  # 30 of 100 @100: cost carried over


def test_a4_same_day_buy_and_sell_in_different_accounts_isnt_intraday() -> None:
    """G holds 10 bought @100 on 2-Jan-25. On 2-Jun-25 Z buys 10 @200 and G sells 10 @210.
    Not intraday: G's sale is delivery against G's lot, STCG 10 x 110 = 1,100; Z's buy stays."""
    r = compute_tax_year(2025, [at(G, buy("2025-01-02", 10, 100)),
                                at(Z, buy("2025-06-02", 10, 200)),
                                at(G, sell("2025-06-02", 10, 210))])
    assert r.business.lines == ()
    assert lines(r) == [(G, date(2025, 1, 2), d(10), "STCG @ 20%", d(1100))]
    assert [(lot.account, lot.acquired_on) for lot in r.open_lots] == [(Z, date(2025, 6, 2))]


def test_a5_moving_more_than_is_held_moves_what_is_held_and_says_so() -> None:
    move = Transfer("T9", date(2024, 9, 2), A, d(150), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2023-01-02", 100, 100))], transfers=[move])
    assert [(lot.account, lot.quantity) for lot in r.open_lots] == [(G, d(100))]
    [note] = [n for n in r.warnings if n.code == "TRANSFER_SHORT"]
    assert "only 100 were held" in note.message


def test_a6_a_bonus_is_allotted_in_each_account_on_its_own() -> None:
    """1:1 bonus, ex-date 2-Jan-25: Z holds 10 → 10 bonus in Z; G holds 20 → 20 in G."""
    bonus = Bonus(A, date(2025, 1, 2), held=1, bonus=1)
    r = compute_tax_year(2025, [at(Z, buy("2024-04-02", 10, 100)),
                                at(G, buy("2024-05-02", 20, 100))], actions=[bonus])
    held = sorted((lot.account, lot.quantity, lot.value) for lot in r.open_lots)
    assert held == [(G, d(20), d(0)), (G, d(20), d(2000)), (Z, d(10), d(0)), (Z, d(10), d(1000))]


def test_a7_one_named_account_behaves_as_before() -> None:
    """Everything in one account: the same result as with no accounts at all, and no
    per-account notice."""
    trades = [buy("2024-01-02", 100, 100), sell("2025-07-01", 40, 250)]
    named = compute_tax_year(2025, [at(Z, t) for t in trades])
    plain = compute_tax_year(2025, trades)
    assert [(x.gain, x.bucket) for x in named.capital_gains] == [
        (x.gain, x.bucket) for x in plain.capital_gains]
    assert not any(n.question == "Q-026" for n in named.warnings)


def test_a8_trades_with_no_account_next_to_named_ones_are_flagged() -> None:
    r = compute_tax_year(2025, [buy("2024-01-02", 100, 100), at(Z, buy("2024-02-02", 1, 1))])
    [note] = [n for n in r.warnings if n.code == "NO_ACCOUNT"]
    assert A in note.message


def test_a9_moving_part_of_a_lot_splits_its_cost() -> None:
    """Z bought 100 @100 (cost 10,000 + charges 50). 60 moved to G: G holds 60 with cost
    6,030 (charges apportioned); Z keeps 40 with cost 4,020."""
    move = Transfer("T2", date(2024, 9, 2), A, d(60), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2023-01-02", 100, 100, charges=50))],
                         transfers=[move])
    assert sorted((lot.account, lot.quantity, lot.cost) for lot in r.open_lots) == [
        (G, d(60), d(6030)), (Z, d(40), d(4020))]


def test_a10_a_transfer_must_be_positive_and_between_two_accounts() -> None:
    import pytest

    with pytest.raises(ValueError, match="positive"):
        Transfer("T", date(2024, 9, 2), A, d(0), Z, G)
    with pytest.raises(ValueError, match="must differ"):
        Transfer("T", date(2024, 9, 2), A, d(1), Z, Z)


def test_a11_a_lot_arriving_after_the_year_end_isnt_held_that_year() -> None:
    """Bought 2-Jan-23 but arrived in G on 2-May-25: not held in G at the end of FY 2024-25."""
    lot = at(G, replace(buy("2023-01-02", 10, 100, trade_id="OPENING:x:1"),
                        entered_on=date(2025, 5, 2)))
    assert compute_tax_year(2024, [lot]).open_lots == ()
    assert [x.quantity for x in compute_tax_year(2025, [lot]).open_lots] == [d(10)]
