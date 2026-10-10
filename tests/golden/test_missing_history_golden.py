"""Missing purchase history (brief 0001 D5): a sale with no earlier purchase is never costed at
zero; the year is incomplete until the user adds the purchase or excludes the sale. Synthetic
data only. Year 2025 = FY 2025-26."""

import pytest

from engine.api import MANUAL_PREFIX, compute_tax_year
from engine.export.itr import ExportError, export_itr
from engine.export.pdf import render_summary
from tests.golden.helpers import A, B, buy, d, sell, taxable

SALE = sell("2025-06-10", 100, 300, trade_id="S1")


def test_h1_sale_with_no_purchase_is_incomplete_and_not_exportable() -> None:
    r = compute_tax_year(2025, [SALE])
    assert not r.complete
    [gap] = r.missing_history
    assert (gap.trade_id, gap.instrument, gap.quantity, gap.sale_value) == (
        "S1", A, d(100), d(30000))
    assert r.capital_gains == ()  # nothing is costed at zero
    assert any(n.code == "MISSING_HISTORY" and A in n.message for n in r.warnings)
    with pytest.raises(ExportError, match="1 sale\\(s\\) are missing purchase history"):
        export_itr(r)
    with pytest.raises(ExportError, match="missing purchase history"):
        render_summary(r)


def test_h2_adding_the_purchase_resolves_it_and_is_flagged_as_manual() -> None:
    """Bought 2023-01-02 @100 → LTCG 20,000, under the ₹1.25 lakh exemption → tax 0."""
    manual = buy("2023-01-02", 100, 100, trade_id=f"{MANUAL_PREFIX}1")
    r = compute_tax_year(2025, [SALE, manual])
    assert r.complete and r.missing_history == ()
    assert taxable(r) == {}
    assert r.capital_gains[0].gain == d(20000)
    assert any(n.code == "MANUAL_PURCHASE" and n.question == "Q-029" for n in r.warnings)
    export_itr(r)  # no longer refused


def test_h3_partial_shortfall_matches_what_is_known() -> None:
    """Known buy of 40 in the file; 60 more sold with no purchase."""
    r = compute_tax_year(2025, [buy("2025-05-01", 40, 250), SALE])
    [gap] = r.missing_history
    assert gap.quantity == d(60)
    assert [line.disposal.quantity for line in r.capital_gains] == [d(40)]


def test_h4_added_older_purchase_is_matched_first_fifo() -> None:
    """With the missing 60 added as an older purchase, FIFO uses it before the known 40, and
    20 of the known lot stay held (the provisional match in h3 is replaced)."""
    trades = [buy("2025-05-01", 40, 250), SALE,
              buy("2020-03-02", 80, 100, trade_id=f"{MANUAL_PREFIX}1")]
    r = compute_tax_year(2025, trades)
    assert r.complete
    assert [(str(line.disposal.acquired_on), line.disposal.quantity)
            for line in r.capital_gains] == [("2020-03-02", d(80)), ("2025-05-01", d(20))]
    assert [(str(lot.acquired_on), lot.quantity) for lot in r.open_lots] == [
        ("2025-05-01", d(20))]


def test_h5_excluding_a_sale_completes_the_year_with_a_label() -> None:
    r = compute_tax_year(2025, [buy("2025-05-01", 10, 50, instrument=B),
                                sell("2025-07-01", 10, 60, instrument=B), SALE],
                         excluded=["S1"])
    assert r.complete and r.missing_history == ()
    assert [s.trade_id for s in r.excluded_sales] == ["S1"]
    assert taxable(r) == {"STCG @ 20%": d(100)}
    [note] = [n for n in r.warnings if n.code == "EXCLUDED"]
    assert "Excludes 1 sale(s) with ₹30000.00 of sale value" in note.message
    assert note.question == "Q-031"
    export_itr(r)


def test_h6_an_earlier_years_gap_keeps_later_years_incomplete() -> None:
    """A gap in FY 2024-25 changes which lots FY 2025-26 sales use, so both stay incomplete.
    Excluded gaps of other years are not listed as this year's exclusions."""
    trades = [buy("2024-05-02", 50, 100), sell("2024-09-02", 80, 120, trade_id="OLD"),
              buy("2025-05-02", 10, 100), sell("2025-08-01", 10, 130)]
    r = compute_tax_year(2025, trades)
    assert not r.complete
    assert [s.trade_id for s in r.missing_history] == ["OLD"]
    excluded = compute_tax_year(2025, trades, excluded=["OLD"])
    assert excluded.complete and excluded.excluded_sales == ()
    assert taxable(excluded) == {"STCG @ 20%": d(300)}


def test_h7_same_day_buy_and_oversell_shortfall_is_the_delivery_part() -> None:
    """Bought 10 and sold 110 on one day with nothing held: 10 are intraday (speculative), the
    other 100 are a delivery sale with no purchase. The shortfall's id is the delivery piece's
    (``S1#delivery``); excluding either that or the sell's own id ``S1`` resolves it."""
    trades = [buy("2025-06-10", 10, 290, trade_id="B1"),
              sell("2025-06-10", 110, 300, trade_id="S1")]
    r = compute_tax_year(2025, trades)
    [gap] = r.missing_history
    assert (gap.trade_id, gap.quantity) == ("S1#delivery", d(100))
    assert len(r.business.lines) == 1
    assert compute_tax_year(2025, trades, excluded=["S1#delivery"]).complete
    excluded = compute_tax_year(2025, trades, excluded=["S1"])
    assert excluded.complete and excluded.excluded_value == d(30000)


def test_h8_manual_buy_never_turns_another_sale_intraday() -> None:
    """A hand-entered purchase is a delivery holding acquired earlier; it must not be squared
    off against an unrelated sale that happens to fall on the date the user typed."""
    trades = [buy("2025-01-01", 5, 100, trade_id="B0"), sell("2025-03-03", 5, 120, trade_id="S0"),
              SALE, buy("2025-03-03", 100, 100, trade_id=f"{MANUAL_PREFIX}1")]
    r = compute_tax_year(2024, trades)
    assert r.business.lines == ()
    assert [line.disposal.quantity for line in r.capital_gains] == [d(5)]
