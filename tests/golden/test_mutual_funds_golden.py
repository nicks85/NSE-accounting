"""Golden cases: mutual fund units. Synthetic data; hand computation in each docstring.
Funds (fake ISINs): EQ_FUND equity-oriented, DEBT_FUND specified (>65% debt), HYBRID_FUND
other. Year 2025 = FY 2025-26 unless noted."""

import pytest

from engine.api import FundClass, compute_tax_year
from engine.matching.fifo import InsufficientHoldingsError
from engine.rules.setoff import LossKind
from tests.golden.helpers import (
    DEBT_FUND,
    EQ_FUND,
    HYBRID_FUND,
    buy,
    d,
    mf,
    sell,
    taxable,
)

CLASSES = {EQ_FUND: FundClass.EQUITY_ORIENTED, DEBT_FUND: FundClass.SPECIFIED,
           HYBRID_FUND: FundClass.OTHER}


def run(year: int, trades: list, **kw: object):  # type: ignore[no-untyped-def]
    return compute_tax_year(year, trades, fund_classes=CLASSES, **kw)  # type: ignore[arg-type]


def test_m01_equity_fund_short_term() -> None:
    """1,000 units @100 (1-May-25) redeemed @110 (1-Oct-25): STCG 10,000 x 20% = 2,000."""
    r = run(2025, [mf("BUY", "2025-05-01", 1000, 100), mf("SELL", "2025-10-01", 1000, 110)])
    assert taxable(r) == {"STCG @ 20%": d(10000)}
    assert r.special_rate_tax_rounded == d(2000)


def test_m02_equity_fund_and_shares_share_one_exemption() -> None:
    """Equity-fund LTCG 1,00,000 + share LTCG 1,00,000 = 2,00,000; one ₹1.25 lakh exemption →
    75,000 x 12.5% = 9,375 → 9,380."""
    r = run(2025, [
        mf("BUY", "2024-01-01", 1000, 100), mf("SELL", "2025-06-01", 1000, 200),
        buy("2024-01-01", 100, 1000), sell("2025-06-01", 100, 2000),
    ])
    assert taxable(r) == {"LTCG @ 12.5%": d(75000)}
    assert r.special_rate_tax_rounded == d(9380)


def test_m03_equity_fund_grandfathering_uses_nav() -> None:
    """1,000 units bought 2016 @20; NAV on 31-Jan-18 = 30; redeemed @50.
    Cost = max(20,000, min(30,000, 50,000)) = 30,000; LTCG 20,000, within exemption."""
    r = run(2025, [mf("BUY", "2016-01-01", 1000, 20), mf("SELL", "2025-06-01", 1000, 50)],
            fmv_2018={EQ_FUND: d(30)})
    line = r.capital_gains[0]
    assert (line.cost, line.gain) == (d(30000), d(20000))


def test_m04_specified_fund_after_april_2023_always_short_term() -> None:
    """TY 2026-27. Debt fund 10,000 units bought 1-May-23 @10, redeemed 1-Jun-26 @13 (3 years):
    still short-term (s.76): 30,000 at slab rates → no special-rate tax."""
    r = run(2026, [mf("BUY", "2023-05-01", 10000, 10, DEBT_FUND),
                   mf("SELL", "2026-06-01", 10000, 13, DEBT_FUND)])
    assert taxable(r) == {"STCG (slab rate)": d(30000)}
    assert r.special_rate_tax_rounded == 0
    assert any("s.76" in c.section(r.pack.act) for c in r.capital_gains[0].citations)


def test_m05_specified_fund_bought_before_april_2023_is_other() -> None:
    """Debt fund bought 1-Jan-22 @10 (before 1-Apr-23), redeemed 1-Jun-25 @15, held > 24
    months: LTCG 50,000 x 12.5% (s.112 / s.197, no exemption) = 6,250 → 6,250."""
    r = run(2025, [mf("BUY", "2022-01-01", 10000, 10, DEBT_FUND),
                   mf("SELL", "2025-06-01", 10000, 15, DEBT_FUND)])
    assert taxable(r) == {"LTCG @ 12.5% (other assets)": d(50000)}
    assert r.setoff.exemption_used == {}
    assert r.special_rate_tax_rounded == d(6250)


def test_m06_other_fund_held_under_24_months_is_slab() -> None:
    """Hybrid fund held 20 months (1-Oct-23 → 1-Jun-25): STCG 5,000 at slab rates."""
    r = run(2025, [mf("BUY", "2023-10-01", 1000, 10, HYBRID_FUND),
                   mf("SELL", "2025-06-01", 1000, 15, HYBRID_FUND)])
    assert taxable(r) == {"STCG (slab rate)": d(5000)}


def test_m07_exemption_only_for_equity_regime() -> None:
    """Hybrid LTCG 1,00,000 (12.5%, no exemption) + equity-fund LTCG 50,000 (exempt).
    Tax = 1,00,000 x 12.5% = 12,500."""
    r = run(2025, [
        mf("BUY", "2022-01-01", 1000, 100, HYBRID_FUND), mf("SELL", "2025-06-01", 1000, 200,
                                                            HYBRID_FUND),
        mf("BUY", "2024-01-01", 1000, 100), mf("SELL", "2025-06-01", 1000, 150),
    ])
    assert taxable(r) == {"LTCG @ 12.5% (other assets)": d(100000)}
    assert r.special_rate_tax_rounded == d(12500)


def test_m08_missing_class_treated_as_other_and_flagged() -> None:
    r = compute_tax_year(2025, [mf("BUY", "2022-01-01", 100, 10), mf("SELL", "2025-06-01", 100,
                                                                      20)])
    assert taxable(r) == {"LTCG @ 12.5% (other assets)": d(1000)}
    assert any(w.code == "FUND_CLASS_MISSING" for w in r.warnings)


def test_m09_pre_cutover_non_equity_redemption_is_manual() -> None:
    """FY 2024-25: hybrid fund redeemed 1-Jul-24 (before 23-Jul-24) → indexed regime, not
    computed: excluded from totals, flagged Q-021."""
    r = run(2024, [mf("BUY", "2020-01-01", 1000, 10, HYBRID_FUND),
                   mf("SELL", "2024-07-01", 1000, 20, HYBRID_FUND)])
    assert r.capital_gains[0].manual
    assert r.bucket_nets == {}
    assert any(w.question == "Q-021" for w in r.warnings)


def test_m10_debt_fund_loss_against_equity_stcg() -> None:
    """Specified-fund STCL 20,000 (slab bucket) set off against equity STCG 50,000 →
    30,000 x 20% = 6,000."""
    r = run(2025, [
        mf("BUY", "2025-04-10", 10000, 10, DEBT_FUND), mf("SELL", "2025-06-10", 10000, 8,
                                                          DEBT_FUND),
        buy("2025-04-10", 100, 1000), sell("2025-06-10", 100, 1500),
    ])
    assert taxable(r) == {"STCG @ 20%": d(30000)}
    assert r.special_rate_tax_rounded == d(6000)


def test_m11_equity_loss_goes_to_slab_rate_gain_first() -> None:
    """Debt-fund STCG 40,000 (slab) and equity STCL 10,000: the loss reduces the slab-rate gain
    (treated as the highest rate, Q-008) → slab STCG 30,000; no special-rate tax."""
    r = run(2025, [
        mf("BUY", "2025-04-10", 10000, 10, DEBT_FUND), mf("SELL", "2025-06-10", 10000, 14,
                                                          DEBT_FUND),
        buy("2025-04-10", 100, 1000), sell("2025-06-10", 100, 900),
    ])
    assert taxable(r) == {"STCG (slab rate)": d(30000)}


def test_m12_fifo_is_per_folio() -> None:
    """Same fund in two folios: folio 1001 bought 2023 @10, folio 2002 bought 2025 @20.
    Redeeming from folio 2002 uses its own lot → STCG (500 x (25-20) = 2,500), flagged Q-020."""
    r = run(2025, [
        mf("BUY", "2023-01-01", 500, 10, folio="1001"),
        mf("BUY", "2025-05-01", 500, 20, folio="2002"),
        mf("SELL", "2025-08-01", 500, 25, folio="2002"),
    ])
    [line] = r.capital_gains
    assert (line.bucket.label, line.gain) == ("STCG @ 20%", d(2500))
    assert any(w.question == "Q-020" for w in r.warnings)


def test_m13_redeeming_more_units_than_held_fails() -> None:
    with pytest.raises(InsufficientHoldingsError):
        run(2025, [mf("BUY", "2025-05-01", 10, 10), mf("SELL", "2025-06-01", 11, 10)])


def test_m14_same_day_fund_buy_and_redeem_is_not_intraday() -> None:
    r = run(2025, [mf("BUY", "2025-06-02", 100, 10), mf("SELL", "2025-06-02", 100, 11)])
    assert r.business.speculative == 0
    assert taxable(r) == {"STCG @ 20%": d(100)}


def test_m15_debt_fund_loss_carried_forward_as_stcl() -> None:
    """Specified-fund loss 20,000 and nothing to absorb it → STCL carried forward."""
    r = run(2025, [mf("BUY", "2025-04-10", 10000, 10, DEBT_FUND),
                   mf("SELL", "2025-06-10", 10000, 8, DEBT_FUND)])
    [cf] = r.carried_forward
    assert (cf.kind, cf.amount) == (LossKind.SHORT_TERM_CAPITAL, d(20000))


def test_m16_fund_without_folio_is_matched_by_isin() -> None:
    """Units identified by ISIN only (e.g. held in demat): no per-folio flag."""
    r = run(2025, [mf("BUY", "2025-05-01", 10, 10, folio=""),
                   mf("SELL", "2025-06-01", 10, 12, folio="")])
    assert r.capital_gains[0].disposal.instrument == EQ_FUND
    assert not any(w.question == "Q-020" for w in r.warnings)


def test_m17_stcl_goes_to_non_exempt_ltcg_first() -> None:
    """QA case. Equity LTCG 1,00,000 (exempt-eligible), hybrid LTCG 1,00,000 (12.5%, not
    exempt), equity STCL 1,00,000. Set the loss against the hybrid gain → hybrid 0; equity
    1,00,000 within the exemption → tax 0 (not 12,500)."""
    r = run(2025, [
        buy("2023-01-01", 100, 1000), sell("2025-06-01", 100, 2000),
        mf("BUY", "2022-01-01", 1000, 100, HYBRID_FUND), mf("SELL", "2025-06-01", 1000, 200,
                                                            HYBRID_FUND),
        buy("2025-04-01", 100, 2000, instrument="INE000C01013"),
        sell("2025-06-01", 100, 1000, instrument="INE000C01013"),
    ])
    assert r.special_rate_tax_rounded == 0
    assert any(w.question == "Q-008" for w in r.warnings)


def test_m18_ltcl_not_netted_inside_exempt_bucket() -> None:
    """QA case. Equity LTCG 1,00,000, equity LTCL 1,00,000, hybrid LTCG 1,00,000. The LTCL goes
    to the hybrid gain; equity LTCG stays within the exemption → tax 0."""
    r = run(2025, [
        buy("2023-01-01", 100, 1000), sell("2025-06-01", 100, 2000),
        buy("2023-01-01", 100, 2000, instrument="INE000C01013"),
        sell("2025-06-01", 100, 1000, instrument="INE000C01013"),
        mf("BUY", "2022-01-01", 1000, 100, HYBRID_FUND), mf("SELL", "2025-06-01", 1000, 200,
                                                            HYBRID_FUND),
    ])
    assert r.special_rate_tax_rounded == 0


def test_m19_short_hold_before_cutover_is_slab_not_manual() -> None:
    """FY 2024-25: hybrid fund held 3 months, redeemed 1-Jul-24 → plain STCG at slab rates."""
    r = run(2024, [mf("BUY", "2024-04-01", 1000, 10, HYBRID_FUND),
                   mf("SELL", "2024-07-01", 1000, 12, HYBRID_FUND)])
    assert not r.capital_gains[0].manual
    assert taxable(r) == {"STCG (slab rate)": d(2000)}


def test_m20_fy2024_25_hybrid_ltcg_after_cutover_and_equity_exemption() -> None:
    """FY 2024-25. Hybrid LTCG 1,00,000 after cutover (12.5%, no exemption) and equity LTCG
    1,00,000 before cutover (10%, exempt up to 1,25,000) → tax 12,500."""
    r = run(2024, [
        mf("BUY", "2020-01-01", 1000, 100, HYBRID_FUND), mf("SELL", "2024-09-01", 1000, 200,
                                                            HYBRID_FUND),
        buy("2023-01-01", 100, 1000), sell("2024-07-01", 100, 2000),
    ])
    assert taxable(r) == {"LTCG @ 12.5% (other assets)": d(100000)}
    assert r.special_rate_tax_rounded == d(12500)


def test_m21_equity_fund_ty2026_27_and_grandfathered_tax() -> None:
    """TY 2026-27: equity-fund LTCG 2,00,000 → 75,000 x 12.5% = 9,375 → 9,380. Plus m03-style
    grandfathering check that tax is 0 when within the exemption."""
    r = run(2026, [mf("BUY", "2024-01-01", 1000, 100), mf("SELL", "2026-06-01", 1000, 300)])
    assert r.special_rate_tax_rounded == d(9380)
    g = run(2025, [mf("BUY", "2016-01-01", 1000, 20), mf("SELL", "2025-06-01", 1000, 50)],
            fmv_2018={EQ_FUND: d(30)})
    assert g.special_rate_tax_rounded == 0


def test_m22_class_can_change_by_year() -> None:
    """A fund treated as specified in FY 2024-25 (old definition) and 'other' in FY 2025-26;
    the specified class before FY 2025-26 is flagged (Q-019)."""
    from engine.api import compute_tax_years

    first, second = compute_tax_years(
        [2024, 2025],
        [mf("BUY", "2023-05-01", 1000, 10, HYBRID_FUND), mf("SELL", "2024-09-01", 500, 12,
                                                            HYBRID_FUND),
         mf("SELL", "2025-09-01", 500, 13, HYBRID_FUND)],
        fund_classes_by_year={2024: {HYBRID_FUND: FundClass.SPECIFIED},
                              2025: {HYBRID_FUND: FundClass.OTHER}})
    assert taxable(first) == {"STCG (slab rate)": d(1000)}
    assert any("35% in domestic equity" in w.message for w in first.warnings)
    assert taxable(second) == {"LTCG @ 12.5% (other assets)": d(1500)}


def test_m23_exchange_traded_gold_etf_uses_fund_rules() -> None:
    """Gold ETF bought on the exchange (INF ISIN, equity segment), classified 'other': held
    20 months → STCG at slab, not 20%. Unclassified → flagged."""
    gold = "INF000G01014"
    trades = [buy("2023-10-01", 100, 50, instrument=gold), sell("2025-06-01", 100, 60,
                                                                instrument=gold)]
    r = compute_tax_year(2025, trades, fund_classes={gold: FundClass.OTHER})
    assert taxable(r) == {"STCG (slab rate)": d(1000)}
    assert any(w.question == "Q-024" for w in r.warnings)
    unflagged = compute_tax_year(2025, trades)
    assert any(w.code == "FUND_CLASS_MISSING" for w in unflagged.warnings)


def test_m24_corporate_action_on_fund_warns() -> None:
    from engine.matching.corporate_actions import Split
    from tests.golden.helpers import day

    r = run(2025, [mf("BUY", "2025-05-01", 10, 10)],
            actions=[Split(EQ_FUND, day("2025-06-01"), old=1, new=10)])
    assert any("Q-023" in w.message for w in r.warnings)
    assert r.open_lots[0].quantity == d(10)


def test_m25_unclassified_funds_lists_open_holdings_too() -> None:
    from engine.api import unclassified_funds

    trades = [mf("BUY", "2025-05-01", 10, 10), mf("BUY", "2025-05-01", 10, 10, DEBT_FUND),
              buy("2025-05-01", 1, 1)]
    assert unclassified_funds(trades, {EQ_FUND: FundClass.EQUITY_ORIENTED}) == [DEBT_FUND]


def test_m26_missing_fmv_for_folio_instrument_and_debt_fund_without_class() -> None:
    """Grandfathered equity fund in a folio without FMV → warning. Post-April-2023 debt fund
    with no class held > 24 months → treated as 'other' LTCG 12.5% and flagged."""
    r = run(2025, [mf("BUY", "2016-01-01", 100, 20), mf("SELL", "2025-06-01", 100, 50)])
    assert any(w.code == "MISSING_FMV" for w in r.warnings)
    other = compute_tax_year(2025, [mf("BUY", "2023-04-01", 100, 10, DEBT_FUND),
                                    mf("SELL", "2025-06-01", 100, 20, DEBT_FUND)])
    assert taxable(other) == {"LTCG @ 12.5% (other assets)": d(1000)}
    assert any(w.code == "FUND_CLASS_MISSING" for w in other.warnings)


def test_m27_exempt_bucket_fully_absorbed_uses_no_exemption() -> None:
    """Equity-fund LTCG 50,000 and share LTCL 50,000: the loss absorbs the gain, so no
    exemption is used and no tax is due."""
    r = run(2025, [mf("BUY", "2024-01-01", 1000, 100), mf("SELL", "2025-06-01", 1000, 150),
                   buy("2023-01-01", 100, 1500), sell("2025-06-01", 100, 1000)])
    assert r.setoff.exemption_used == {}
    assert r.special_rate_tax_rounded == 0
