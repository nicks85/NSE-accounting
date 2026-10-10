"""Filed years and the "changed since filing" list (brief 0001 task 4). Synthetic data."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.ledger import Batch, Ledger, LedgerError
from engine.ledger.filing import changes, figures
from engine.ledger.settings import Settings
from engine.rpc import report_to_json
from engine.rules.setoff import LossEntry, LossKind
from tests.golden.helpers import B, buy, d, sell


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger.open(tmp_path / "kosh.sqlite") as opened:
        yield opened


@pytest.fixture
def person(ledger: Ledger) -> int:
    return ledger.ensure_profile("Synthetic").id


FIRST = [buy("2025-05-01", 100, 1000, trade_id="Z:1"),
         sell("2025-07-01", 100, 1100, trade_id="Z:2")]
"""STCG 10,000 → tax 2,000."""


def report(ledger: Ledger, person: int) -> dict:  # type: ignore[type-arg]
    return report_to_json(ledger.compute(person, [2025])[0])


def test_mark_filed_keeps_the_figures_and_lists_later_changes(ledger: Ledger,
                                                              person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), FIRST)
    filed_report = report(ledger, person)
    when = ledger.mark_filed(person, 2025, filed_report, "ITR-2")
    filed = ledger.filed(person, 2025)
    assert filed is not None and (filed.filed_at, filed.itr_form) == (when, "ITR-2")
    assert filed.report == filed_report
    assert ledger.filed_years(person) == [2025]
    assert changes(filed.report, report(ledger, person)) == []

    # An older purchase turns up: a second sale on B adds STCG 5,000 → tax 3,000.
    ledger.import_trades(person, Batch(file_sha256="b"), [
        buy("2025-05-02", 10, 500, instrument=B, trade_id="Z:3"),
        sell("2025-08-01", 10, 1000, instrument=B, trade_id="Z:4")])
    diff = {c["item"]: (c["filed"], c["now"]) for c in changes(filed.report,
                                                                report(ledger, person))}
    assert diff["Tax at special rates (rounded)"] == ("2000", "3000")
    assert diff["Taxable STCG @ 20%"] == ("10000", "15000")
    assert diff["Number of capital-gain lines"] == ("1", "2")


def test_unmark_forgets_the_filing(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), FIRST)
    ledger.mark_filed(person, 2025, report(ledger, person))
    ledger.unmark_filed(person, 2025)
    assert ledger.filed(person, 2025) is None and ledger.filed_years(person) == []


def test_an_incomplete_year_cannot_be_marked_filed(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), [sell("2025-07-01", 1, 1, trade_id="S")])
    with pytest.raises(LedgerError, match="missing purchase history"):
        ledger.mark_filed(person, 2025, report(ledger, person))


def test_filing_and_on_time_answers_live_side_by_side(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), FIRST)
    ledger.mark_filed(person, 2025, report(ledger, person))
    ledger.save_settings(person, Settings(filed_on_time={2024: False, 2025: True}))
    ledger.save_settings(person, Settings(filed_on_time={2024: True}))
    assert dict(ledger.settings(person).filed_on_time) == {2024: True}
    assert ledger.filed(person, 2025) is not None  # saving settings never clears a filing


def test_compute_applies_late_returns(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), FIRST)
    loss = (LossEntry(2024, LossKind.SHORT_TERM_CAPITAL, d(4000)),)
    ledger.save_settings(person, Settings(brought_forward=loss, filed_on_time={2024: True}))
    assert ledger.compute(person, [2025])[0].special_rate_tax_rounded == d(1200)
    ledger.save_settings(person, Settings(brought_forward=loss, filed_on_time={2024: False}))
    late = ledger.compute(person, [2025])[0]
    assert late.special_rate_tax_rounded == d(2000) and late.lapsed == loss
    assert Settings(filed_on_time={2023: False, 2024: True, 2022: False}).late_returns == (
        2022, 2023)


def test_changes_ignore_zero_lines_and_format() -> None:
    base = {"summary": {"bucket_nets": [{"label": "STCG @ 20%", "amount": "100.00"}],
                        "exemption_used": [], "taxable": [],
                        "special_rate_tax_rounded": "0", "speculative_after_setoff": "0",
                        "non_speculative_after_setoff": "0", "speculative_income": "0",
                        "non_speculative_income": "0"},
            "carried_forward": [], "capital_gains": [], "business_lines": [],
            "setoff_steps": []}
    same = {**base, "summary": {**base["summary"], "bucket_nets": [
        {"label": "STCG @ 20%", "amount": "100"}, {"label": "LTCG @ 12.5%", "amount": "0"}]}}
    assert changes(base, same) == []
    gone = {**base, "summary": {**base["summary"], "bucket_nets": []},
            "carried_forward": [{"origin_year": 2025, "kind": "Short-term capital loss",
                                 "amount": "50"}]}
    assert changes(base, gone) == [
        {"item": "Net STCG @ 20%", "filed": "100.00", "now": None},
        {"item": "Carried forward: Short-term capital loss of FY 2025-26", "filed": None,
         "now": "50"}]
    assert "Net STCG @ 20%" in figures(base)


def test_two_losses_of_one_kind_and_year_are_added_up() -> None:
    """QA: 100 + 50 changing to 900 + 50 must show as a change."""
    def with_losses(*amounts: str) -> dict:  # type: ignore[type-arg]
        return {"summary": {"bucket_nets": [], "exemption_used": [], "taxable": [],
                            "special_rate_tax_rounded": "0", "speculative_after_setoff": "0",
                            "non_speculative_after_setoff": "0", "speculative_income": "0",
                            "non_speculative_income": "0"},
                "carried_forward": [{"origin_year": 2024, "kind": "Short-term capital loss",
                                     "amount": a} for a in amounts],
                "capital_gains": [], "business_lines": [],
                "setoff_steps": [{"loss": "Brought-forward short-term capital loss of 2023",
                                  "amount": a} for a in amounts]}
    diff = changes(with_losses("100", "50"), with_losses("900", "50"))
    assert [(c["item"], c["filed"], c["now"]) for c in diff] == [
        ("Set off: Brought-forward short-term capital loss of 2023", "150", "950"),
        ("Carried forward: Short-term capital loss of FY 2024-25", "150", "950")]


def test_sale_value_and_cost_moving_together_is_a_change(ledger: Ledger, person: int) -> None:
    ledger.import_trades(person, Batch(file_sha256="a"), FIRST)
    filed = report(ledger, person)
    ledger.save_settings(person, Settings())  # nothing changes
    assert changes(filed, report(ledger, person)) == []
    moved = {**filed, "capital_gains": [{**filed["capital_gains"][0], "sale_value": "120000",
                                         "cost": "110000"}]}
    items = {c["item"] for c in changes(filed, moved)}
    assert items == {"Total sale value", "Total cost"}


def test_unmark_needs_a_known_profile(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="no profile"):
        ledger.unmark_filed(99, 2025)
