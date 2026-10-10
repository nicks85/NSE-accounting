"""QA review of brief 0006 (residential status). Synthetic data only.

Checks that the status never changes a figure or a line's citations in any supported year,
that the setting survives a full settings replace, a filing and a backup/restore, and records
review findings (formerly strict xfails, fixed in the task 13 round)."""

from pathlib import Path
from typing import Any

import pytest

from engine.api import compute_tax_year
from engine.ledger import Batch, Ledger
from engine.ledger.filing import changes
from engine.ledger.settings import Settings
from engine.rpc import report_to_json
from engine.rules import common
from tests.golden.helpers import B, buy, fut, sell


def _trades(y: int) -> list[Any]:
    """LTCG on A, STCG on B, intraday on B and an F&O round trip in year ``y``: every bucket
    the year can hold."""
    return [
        buy("2023-04-03", 100, 1000), sell(f"{y}-07-01", 100, 2600),
        buy(f"{y}-04-10", 50, 400, instrument=B), sell(f"{y}-09-10", 50, 520, instrument=B),
        buy(f"{y}-10-01", 10, 500, instrument=B), sell(f"{y}-10-01", 10, 510, instrument=B),
        fut("BUY", f"{y}-05-02", 50, 20000), fut("SELL", f"{y}-05-20", 50, 20100),
    ]


def _without_status(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out.pop("residency")
    out["warnings"] = [w for w in out["warnings"]
                       if w["code"] != "RESIDENCY" and w.get("question") != "Q-028"]
    return out


@pytest.mark.parametrize("year", [2024, 2025, 2026])
@pytest.mark.parametrize("status", ["NOR", "NRI"])
def test_qa1_no_figure_or_line_citation_changes_in_any_year(year: int, status: str) -> None:
    trades = _trades(year)
    resident = report_to_json(compute_tax_year(year, trades))
    other = report_to_json(compute_tax_year(year, trades, residency=status))
    assert resident["summary"]["special_rate_tax_rounded"] != "0"  # the case has real tax
    assert _without_status(other) == _without_status(resident)


def test_qa2_status_survives_filing_and_a_full_replace_keeps_the_filing(tmp_path: Path) -> None:
    trades = _trades(2025)
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        me = ledger.ensure_profile("Synthetic").id
        other = ledger.ensure_profile("Other").id
        ledger.import_trades(me, Batch(file_sha256="a"), trades)
        ledger.save_settings(other, Settings(residency={2025: "NOR"}))
        ledger.save_settings(me, Settings(residency={2025: "NRI", 2024: "NOR"},
                                          filed_on_time={2025: False}))
        [report] = ledger.compute(me, [2025])
        ledger.mark_filed(me, 2025, report_to_json(report), "ITR-2")
        # A later full replace without the status resets it, keeps the filing and on-time flag.
        ledger.save_settings(me, Settings(filed_on_time={2025: False}))
        assert dict(ledger.settings(me).residency) == {}
        assert dict(ledger.settings(me).filed_on_time) == {2025: False}
        assert ledger.filed_years(me) == [2025]
        filed = ledger.filed(me, 2025)
        assert filed is not None and filed.report["residency"] == "NRI"
        # Another person's status is untouched by this person's saves.
        assert dict(ledger.settings(other).residency) == {2025: "NOR"}
        # Setting it back on a year that has a filed row updates that row in place.
        ledger.save_settings(me, Settings(residency={2025: "NRI"}))
        assert dict(ledger.settings(me).residency) == {2025: "NRI"}
        assert ledger.filed_years(me) == [2025]
        assert dict(ledger.settings(me).filed_on_time) == {}


def test_qa3_backup_and_restore_keep_the_status(tmp_path: Path) -> None:
    with Ledger.open(tmp_path / "k.sqlite") as ledger:
        me = ledger.ensure_profile("Synthetic").id
        ledger.save_settings(me, Settings(residency={2026: "NRI"}))
        data = ledger.backup()
        ledger.save_settings(me, Settings())
        assert dict(ledger.settings(me).residency) == {}
        ledger.restore(data)
        assert dict(ledger.settings(me).residency) == {2026: "NRI"}


def test_qa4_non_resident_notice_cites_the_1961_act_in_a_1961_year() -> None:
    r = compute_tax_year(2024, _trades(2024), residency="NRI")
    note = next(n.message for n in r.warnings if n.code == "RESIDENCY")
    assert "1961" in note and "s.87A" in note


def test_qa5_rnor_citation_has_the_2025_act_section() -> None:
    assert common.RNOR_AS_RESIDENT.act_2025 is not None


def test_qa6_changing_the_status_after_filing_is_flagged() -> None:
    trades = _trades(2025)
    filed = report_to_json(compute_tax_year(2025, trades))
    now = report_to_json(compute_tax_year(2025, trades, residency="NRI"))
    assert changes(filed, now)
