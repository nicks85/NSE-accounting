"""Golden cases: losses from a return filed late don't carry forward (Q-011; 2025 Act s.121,
1961 Act s.80 with s.139(3)). Synthetic data. Year 2025 = FY 2025-26."""

from engine.api import compute_tax_year, compute_tax_years
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import B, buy, d, sell, taxable

GAIN = [buy("2025-05-01", 100, 1000, instrument=B), sell("2025-07-01", 100, 1100, instrument=B)]
"""STCG 10,000 in FY 2025-26."""
STCL_2024 = LossEntry(2024, LossKind.SHORT_TERM_CAPITAL, d(4000))


def test_l1_loss_from_a_return_filed_on_time_is_set_off() -> None:
    """STCG 10,000 - b/f STCL 4,000 = 6,000 x 20% = 1,200."""
    r = compute_tax_year(2025, GAIN, brought_forward=[STCL_2024])
    assert taxable(r) == {"STCG @ 20%": d(6000)}
    assert r.lapsed == ()


def test_l2_loss_from_a_late_return_is_not_set_off() -> None:
    """FY 2024-25 return filed late: its STCL 4,000 can't be set off. STCG 10,000 x 20%."""
    r = compute_tax_year(2025, GAIN, brought_forward=[STCL_2024], late_returns=[2024])
    assert taxable(r) == {"STCG @ 20%": d(10000)}
    assert r.special_rate_tax_rounded == d(2000)
    assert r.lapsed == (STCL_2024,)
    [note] = [n for n in r.warnings if n.code == "LOSS_LAPSED"]
    assert "FY 2024-25" in note.message and note.question == "Q-011"
    assert r.carried_forward == ()


def test_l3_this_years_late_return_flags_its_own_losses() -> None:
    """STCL 5,000 in FY 2025-26 with this year's return marked late: it isn't carried
    forward at all (not listed in carried_forward), so the next year doesn't set it off."""
    loss = [buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 950)]
    next_gain = [buy("2026-05-01", 10, 1000, instrument=B),
                 sell("2026-07-01", 10, 2000, instrument=B)]
    on_time, then = compute_tax_years([2025, 2026], loss + next_gain)
    assert taxable(then) == {"STCG @ 20%": d(5000)}
    late, after = compute_tax_years([2025, 2026], loss + next_gain, late_returns=[2025])
    assert any(n.code == "NOT_CARRIED" for n in late.warnings)
    assert not any(n.code == "NOT_CARRIED" for n in on_time.warnings)
    assert [e.amount for e in on_time.carried_forward] == [d(5000)]
    assert late.carried_forward == () and [e.amount for e in late.not_carried] == [d(5000)]
    assert taxable(after) == {"STCG @ 20%": d(10000)}


def test_l4_a_late_year_in_between_does_not_lapse_an_earlier_years_loss() -> None:
    """FY 2024-25 STCL 4,000 filed on time; FY 2025-26 return late with no loss of its own.
    s.80 / s.121 bar only the late year's own loss, so FY 2026-27 still sets off the 4,000:
    STCG 10,000 - 4,000 = 6,000."""
    later_gain = [buy("2026-05-01", 100, 1000, instrument=B),
                  sell("2026-07-01", 100, 1100, instrument=B)]
    _, after = compute_tax_years([2025, 2026], later_gain, brought_forward=[STCL_2024],
                                 late_returns=[2025])
    assert taxable(after) == {"STCG @ 20%": d(6000)}
    assert after.lapsed == ()
