"""QA review of brief 0004 (import preview): places where the preview and the save diverge.

All of them were open defects in the first review; the preview now rehearses the save, so
they pass."""
import base64
import hashlib
from pathlib import Path

from engine.rpc import handle
from tests.fixtures import angel_one as ao
from tests.fixtures.zerodha import Row, tradebook_csv


def call(method: str, **params: object) -> dict:  # type: ignore[type-arg]
    return handle({"id": 1, "method": method, "params": params})


def ok(method: str, **params: object) -> dict:  # type: ignore[type-arg]
    reply = call(method, **params)
    assert "result" in reply, reply
    return reply["result"]  # type: ignore[no-any-return]


def zfile(name: str, rows: list[Row]) -> dict[str, str]:
    return {"name": name, "data_base64": base64.b64encode(tradebook_csv(rows).encode()).decode()}


APRIL = [Row("SYNTHA", "2025-04-02", "buy", "10", "100"),
         Row("SYNTHA", "2025-04-03", "buy", "5", "110")]
MAY = Row("SYNTHA", "2025-05-02", "sell", "15", "120")


def _added(reply: dict) -> list[int]:  # type: ignore[type-arg]
    return [f["added"] for f in reply["files"]]


def test_overlapping_files_in_one_request_preview_as_save_does(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    files = [zfile("apr.csv", APRIL), zfile("apr-may.csv", [*APRIL, MAY])]
    preview = ok("ledger_import", profile_id=person, broker="zerodha", mode="preview",
                 files=files)
    saved = ok("ledger_import", profile_id=person, broker="zerodha", files=files,
               expected=[f["sha256"] for f in preview["files"]])
    assert _added(saved) == [2, 1]
    assert _added(preview) == _added(saved)  # preview says [2, 3]


def test_conflict_between_files_in_one_request_is_shown_in_the_preview(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    changed = [APRIL[0], Row("SYNTHA", "2025-04-03", "buy", "5", "111")]  # same trade number
    files = [zfile("a.csv", APRIL), zfile("b.csv", changed)]
    preview = ok("ledger_import", profile_id=person, broker="zerodha", mode="preview",
                 files=files)
    saved = ok("ledger_import", profile_id=person, broker="zerodha", files=files)
    assert saved["files"][1]["conflicts"]
    assert preview["files"][1]["conflicts"]  # preview: no conflicts, "Save all 2 files"


def test_same_file_twice_in_one_request(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    one = zfile("apr.csv", APRIL)
    files = [one, {**one, "name": "apr (1).csv"}]
    preview = ok("ledger_import", profile_id=person, broker="zerodha", mode="preview",
                 files=files)
    saved = ok("ledger_import", profile_id=person, broker="zerodha", files=files)
    assert _added(saved) == [2, 0]
    assert _added(preview) == _added(saved)


def test_expected_checks_every_file_even_with_the_same_name(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    a, b, c = zfile("t.csv", APRIL), zfile("t.csv", [MAY]), zfile("t.csv", [APRIL[0]])
    preview = ok("ledger_import", profile_id=person, broker="zerodha", mode="preview",
                 files=[a, b])
    expected = [f["sha256"] for f in preview["files"]]
    reply = call("ledger_import", profile_id=person, broker="zerodha", files=[c, b],
                 expected=expected)  # file 1 was never previewed
    assert "error" in reply


def test_skipped_rows_do_not_make_the_charges_check_differ(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    rows = [ao.Row("SYNTHETIC ALPHA LTD", "BUY", "100", 10, "2025-05-02", "1", brokerage="5"),
            ao.Row("SYNTHETIC FUT", "BUY", "100", 10, "2025-05-02", "2", brokerage="20",
                   segment="NFO")]
    data = ao.trades_csv(rows)  # Total Trade Charges = 25, as the broker states it
    preview = ok("ledger_import", profile_id=person, broker="angelone", mode="preview",
                 files=[{"name": "a.csv", "data_base64": base64.b64encode(data).decode()}])
    [summary] = preview["files"]
    assert not any("Total Trade Charges" in n for n in summary["notes"])  # importer: fine
    assert any("skipped" in n for n in summary["notes"])
    assert summary["charges_check"] == "matches"  # preview: "differs" (5 vs 25)


def test_save_without_expected_still_works_and_sha_is_reported(ledger_dir: Path) -> None:
    """Back-compat: save with no ``expected`` saves; every file reports its SHA-256."""
    person = ok("ledger_profile")["profile"]["id"]
    one = zfile("apr.csv", APRIL)
    first = ok("ledger_import", profile_id=person, broker="zerodha", files=[one])
    assert first["saved"] is True and _added(first) == [2]
    again = ok("ledger_import", profile_id=person, broker="zerodha", mode="preview", files=[one])
    sha = hashlib.sha256(base64.b64decode(one["data_base64"])).hexdigest()
    assert again["files"][0]["sha256"] == sha and again["files"][0]["already_imported_on"]
    assert len(again["trades"]) == 2


def test_preview_stores_nothing_for_conflicts_and_opening_corrections(ledger_dir: Path) -> None:
    """dry_run follows the same refusal path as save: a conflicting file previews as refused."""
    person = ok("ledger_profile")["profile"]["id"]
    ok("ledger_import", profile_id=person, broker="zerodha", files=[zfile("a.csv", APRIL)])
    changed = [Row("SYNTHA", "2025-04-02", "buy", "10", "101")]
    preview = ok("ledger_import", profile_id=person, broker="zerodha", mode="preview",
                 files=[zfile("b.csv", changed)])
    assert preview["files"][0]["conflicts"] and preview["files"][0]["added"] == 0
    assert len(preview["trades"]) == 2 and len(preview["batches"]) == 1
