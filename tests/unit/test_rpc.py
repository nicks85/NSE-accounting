import base64
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from engine.rpc import handle, trade_to_json
from tests.fixtures.tradebooks import UPSTOX_HEADER, upstox_row, write_csv
from tests.fixtures.zerodha import Row, tradebook_csv
from tests.golden.helpers import DEBT_FUND, A, buy, mf, sell

ROOT = Path(__file__).resolve().parents[2]
PAN = "ABCDE1234F"  # the repo's only (synthetic) PAN


def call(method: str, **params: object) -> dict:  # type: ignore[type-arg]
    response = handle({"id": 7, "method": method, "params": params})
    assert response["id"] == 7
    return response


def ok(method: str, **params: object) -> dict:  # type: ignore[type-arg]
    response = call(method, **params)
    assert "error" not in response, response
    return response["result"]  # type: ignore[no-any-return]


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


TRADES = [trade_to_json(t) for t in (buy("2015-01-01", 10, 100), sell("2025-06-01", 10, 300))]


def test_version_and_compute() -> None:
    assert ok("version")["version"]
    assert handle({"id": 7, "method": "version", "params": None})["result"]
    report = ok("compute", year=2025, trades=TRADES, fmv_2018={A: "150"})
    line = report["capital_gains"][0]
    assert (Decimal(line["cost"]), Decimal(line["gain"]), line["bucket"]) == (
        Decimal(1500), Decimal(1500), "LTCG @ 12.5%")
    assert any(c["section"] == "s.112A" for c in line["citations"])
    assert report["tax_year"] == "FY 2025-26"
    assert Decimal(report["summary"]["special_rate_tax"]) == 0
    assert report["open_lots"] == []


def test_amounts_round_trip_as_exact_strings() -> None:
    trades = [trade_to_json(t) for t in (buy("2025-05-01", 3, "333.3333"),
                                         sell("2025-06-01", 3, "333.3334"))]
    gain = ok("compute", year=2025, trades=trades)["capital_gains"][0]["gain"]
    assert Decimal(gain) == Decimal("0.0003")


def test_import_zerodha_upstox_and_mapped() -> None:
    zerodha = ok("import", broker="zerodha", files=[{"name": "z.csv", "data_base64": b64(
        tradebook_csv([Row("SYNTHA", "2025-05-01", "buy", "1", "1")]))}])
    assert len(zerodha["trades"]) == 1 and not zerodha["format_confirmed"]
    upstox = ok("import", broker="upstox", files=[{"name": "u.csv", "data_base64": b64(
        write_csv(UPSTOX_HEADER, [upstox_row("2025-05-02", "BUY", 1, "1")]))}])
    assert upstox["trades"][0]["trade_id"].startswith("UPSTOX:")
    mapped = ok("import", broker="mapped", key="GROWW", source="Groww (mapped)",
                mapping={"trade_date": "d", "side": "s", "quantity": "q", "price": "p",
                         "trade_id": "id", "isin": "isin", "exchange": "ex"},
                files=[{"name": "g.csv", "data_base64": b64(write_csv(
                    ["d", "s", "q", "p", "id", "isin", "ex"],
                    [["2025-05-01", "B", "1", "1", "G1", A, "NSE"]]))}])
    assert mapped["trades"][0]["trade_id"] == "GROWW:NSE:2025-05-01:G1"


def test_import_angel_one_by_name_or_isin() -> None:
    from tests.fixtures.angel_one import ISINS, SYNTH_A, Row, trades_xlsx

    data = base64.b64encode(trades_xlsx(
        [Row("SYNTHETIC ALPHA LTD", "Buy", "10", 2, "2025-05-02", "1", brokerage="1")])).decode()
    files = [{"name": "a.xlsx", "data_base64": data}]
    by_name = ok("import", broker="angelone", files=files)
    assert by_name["trades"][0]["instrument"] == "NAME:SYNTHETIC ALPHA LTD"
    assert by_name["scheme_names"] == {"NAME:SYNTHETIC ALPHA LTD": "SYNTHETIC ALPHA LTD"}
    result = ok("import", broker="angelone", files=files, isin_map=ISINS)
    assert result["trades"][0]["instrument"] == SYNTH_A
    assert result["trades"][0]["charges"] == "1"
    assert result["format_confirmed"]
    bad = call("import", broker="angelone", files=files, isin_map=["x"])
    assert "isin_map must be an object" in bad["error"]["message"]


def test_import_cas_through_casparser(monkeypatch: pytest.MonkeyPatch) -> None:
    import casparser

    from tests.fixtures.cas import cas, scheme, txn

    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *_, **__: cas(("9", [scheme(
        "Synthetic Equity Fund", "INF000E01011", [txn("2025-05-02", "PURCHASE", "10", "10")])])))
    result = ok("import", broker="cas", password=PAN,
                files=[{"name": "cas.pdf", "data_base64": b64("%PDF")}])
    assert result["suggested_classes"] == {"INF000E01011": "equity-oriented"}
    assert result["scheme_names"]["INF000E01011"] == "Synthetic Equity Fund"
    two = call("import", broker="cas", files=[{"name": "a", "data_base64": b64("x")}] * 2)
    assert "one CAS PDF" in two["error"]["message"]


def test_unclassified_exports_and_corporate_actions() -> None:
    trades = [trade_to_json(t) for t in (mf("BUY", "2025-05-01", 10, 10, DEBT_FUND),)]
    assert ok("unclassified_funds", trades=trades)["isins"] == [DEBT_FUND]
    itr = ok("export_itr", year=2025, trades=TRADES, fmv_2018={A: "150"}, names={A: "SYN A"})
    assert itr["valid"] and itr["form"] == "ITR-2"
    rows = json.loads(itr["json"])["Schedule112A"]["Schedule112ADtls"]
    assert rows[0]["ShareUnitName"] == "SYN A"
    pdf = ok("export_pdf", year=2025, trades=TRADES)
    assert base64.b64decode(pdf["pdf_base64"]).startswith(b"%PDF")
    actions = [{"kind": "split", "instrument": A, "ex_date": "2020-01-01", "old": 1, "new": 2},
               {"kind": "bonus", "instrument": A, "ex_date": "2021-01-01", "held": 1, "bonus": 1,
                "allotment_date": "2021-01-03", "record_date": "2021-01-01"}]
    report = ok("compute", year=2025, trades=TRADES[:1], actions=actions,
                brought_forward=[{"origin_year": 2024, "kind": "Short-term capital loss",
                                  "amount": "5"}])
    assert [lot["quantity"] for lot in report["open_lots"]] == ["20", "20"]
    assert report["carried_forward"][0]["amount"] == "5"


@pytest.mark.parametrize(("request_", "message"), [
    ([], "must be a JSON object"),
    ({"method": "nope"}, "unknown method"),
    ({"method": "compute", "params": []}, "params must be an object"),
    ({"method": "import", "params": {"broker": "x", "files": [{"name": "a",
                                                                "data_base64": "eA=="}]}},
     "unknown broker"),
    ({"method": "import", "params": {"broker": "zerodha"}}, "no files"),
    ({"method": "import", "params": {"broker": "zerodha", "files": [{"name": "a"}]}},
     "name and base64"),
    ({"method": "compute", "params": {"year": 2025, "trades": [{"trade_id": "x"}]}},
     "missing 'trade_date'"),
    ({"method": "compute", "params": {"year": 2025, "trades": [{**TRADES[0], "price": 1.5}]}},
     "decimal string"),
    ({"method": "compute", "params": {"year": 2025, "trades": [{**TRADES[0], "price": "abc"}]}},
     "decimal string"),
    ({"method": "compute", "params": {"year": 2025, "trades": [{**TRADES[0], "price": "NaN"}]}},
     "finite"),
    ({"method": "compute", "params": {"year": 2025, "trades": [{**TRADES[0],
                                                                 "trade_date": "05/01"}]}},
     "ISO date"),
    ({"method": "compute", "params": {"year": 2025, "actions": [{"kind": "merger"}]}},
     "unknown corporate action"),
    ({"method": "compute", "params": {"year": 2025, "brought_forward": [{"kind": "x"}]}},
     "missing"),
    ({"method": "compute", "params": {"year": 2023}}, "not supported"),
])
def test_errors_come_back_as_data(request_: object, message: str) -> None:
    response = handle(request_)
    assert message in response["error"]["message"], response


def test_stdin_stdout_process() -> None:
    lines = "\n".join([
        json.dumps({"id": 1, "method": "version"}),
        "",
        "not json",
        json.dumps({"id": 2, "method": "compute", "params": {"year": 2025, "trades": TRADES}}),
    ]) + "\n"
    out = subprocess.run([sys.executable, "-m", "engine.rpc"], input=lines,
                         capture_output=True, text=True, check=True, cwd=ROOT)
    responses = [json.loads(line) for line in out.stdout.splitlines()]
    assert [r.get("id") for r in responses] == [1, None, 2]
    assert responses[1]["error"]["type"] == "JSONDecodeError"
    assert responses[2]["result"]["capital_gains"][0]["sold_on"] == "2025-06-01"


def test_main_loop_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from engine import rpc

    monkeypatch.setattr(sys, "stdin", io.StringIO('{"id": 3, "method": "version"}\n\nbad\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    rpc.main()
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert responses[0]["id"] == 3 and "version" in responses[0]["result"]
    assert responses[1]["error"]["type"] == "JSONDecodeError"


def test_utf8_on_a_cp1252_locale() -> None:
    """QA case: Windows' default cp1252 can't encode ₹; the engine must still answer."""
    import os

    request = json.dumps({"id": 1, "method": "compute", "params": {
        "year": 2025, "trades": TRADES[:1], "brought_forward": [
            {"origin_year": 2016, "kind": "Short-term capital loss", "amount": "5"}]}},
        ensure_ascii=False) + "\n"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    out = subprocess.run([sys.executable, "-m", "engine.rpc"], input=request.encode("utf-8"),
                         capture_output=True, check=True, cwd=ROOT, env=env)
    response = json.loads(out.stdout.decode("utf-8"))
    assert any("₹5" in w["message"] for w in response["result"]["warnings"])


def test_import_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import rpc

    monkeypatch.setattr(rpc, "MAX_IMPORT_BYTES", 3)
    response = call("import", broker="zerodha", files=[{"name": "a", "data_base64": b64("abcd")}])
    assert "larger than" in response["error"]["message"]


def test_compute_reports_missing_history_and_exclusions() -> None:
    sale = trade_to_json(sell("2025-06-01", 5, 300, trade_id="S9"))
    report = ok("compute", year=2025, trades=[sale])
    assert report["complete"] is False
    assert report["missing_history"] == [{
        "trade_id": "S9", "instrument": A, "isin": A, "sold_on": "2025-06-01", "quantity": "5",
        "price": "300", "sale_value": "1500", "segment": "EQUITY", "account": None}]
    excluded = ok("compute", year=2025, trades=[sale], excluded=["S9"])
    assert excluded["complete"] is True and excluded["missing_history"] == []
    assert excluded["excluded_sales"][0]["trade_id"] == "S9"
    assert excluded["excluded_value"] == "1500"
    refused = call("export_itr", year=2025, trades=[sale])
    assert "missing purchase history" in refused["error"]["message"]


def zerodha_file(name: str, rows: list[Row]) -> dict[str, str]:
    return {"name": name, "data_base64": b64(tradebook_csv(rows))}


APRIL = [Row("SYNTHA", "2025-04-02", "buy", "10", "100"),
         Row("SYNTHA", "2025-04-03", "buy", "5", "110")]


def test_ledger_profile_import_dedupe_and_undo(ledger_dir: Path) -> None:
    opened = ok("ledger_profile", name="Synthetic")
    person = opened["profile"]["id"]
    assert (opened["trades"], opened["batches"]) == ([], [])
    assert ok("ledger_profile")["profile"]["name"] == "Me"
    first = ok("ledger_import", profile_id=person, broker="zerodha",
               files=[zerodha_file("apr.csv", APRIL)])
    assert first["files"] == [{"name": "apr.csv", "added": 2, "duplicates": 0,
                               "already_imported_on": None, "conflicts": [],
                               "possible_duplicates": 0}]
    assert len(first["trades"]) == 2 and first["batches"][0]["broker"] == "zerodha"
    assert (ledger_dir / "kosh.sqlite").is_file()

    again = ok("ledger_import", profile_id=person, broker="zerodha",
               files=[zerodha_file("apr.csv", APRIL), zerodha_file("all.csv", [
                   *APRIL, Row("SYNTHA", "2025-05-02", "sell", "15", "120")])])
    assert again["files"][0]["already_imported_on"] is not None
    assert (again["files"][1]["added"], again["files"][1]["duplicates"]) == (1, 2)
    assert len(again["trades"]) == 3 and len(again["batches"]) == 2

    state = ok("ledger_state", profile_id=person)
    assert state["trades"] == again["trades"]
    undone = ok("ledger_undo", profile_id=person, batch_id=again["batches"][1]["id"])
    assert undone["removed"] == 1 and len(undone["trades"]) == 2


def test_ledger_import_reports_conflicts(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    ok("ledger_import", profile_id=person, broker="zerodha", files=[zerodha_file("a.csv", APRIL)])
    changed = [Row("SYNTHA", "2025-04-02", "buy", "10", "101"), *APRIL[1:]]
    result = ok("ledger_import", profile_id=person, broker="zerodha",
                files=[zerodha_file("b.csv", changed)])
    [conflict] = result["files"][0]["conflicts"]
    assert (conflict["new"]["price"], conflict["existing"]["price"]) == ("101", "100")
    assert len(result["trades"]) == 2 and result["warnings"] == []


def test_ledger_requests_are_checked(ledger_dir: Path) -> None:
    assert "profile_id" in call("ledger_state")["error"]["message"]
    assert "profile_id" in call("ledger_state", profile_id=True)["error"]["message"]
    assert "batch_id" in call("ledger_undo", profile_id=1, batch_id="1")["error"]["message"]
    person = ok("ledger_profile")["profile"]["id"]
    assert "no import" in call("ledger_undo", profile_id=person, batch_id=99)["error"]["message"]


def test_a_bad_file_saves_nothing_from_the_same_request(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    bad = {"name": "bad.csv", "data_base64": b64("not,a,tradebook\n")}
    assert "error" in call("ledger_import", profile_id=person, broker="zerodha",
                           files=[zerodha_file("good.csv", APRIL), bad])
    assert ok("ledger_state", profile_id=person)["trades"] == []


def test_the_same_trades_under_another_mapped_name_are_refused(ledger_dir: Path) -> None:
    """A mapped file's ids use the broker name the user types; renaming it must not double
    count (QA, task 2)."""
    person = ok("ledger_profile")["profile"]["id"]
    mapping = {"trade_date": "trade_date", "side": "trade_type", "quantity": "quantity",
               "price": "price", "trade_id": "trade_id", "isin": "isin", "exchange": "exchange",
               "segment": "segment", "executed_at": "order_execution_time"}
    first = ok("ledger_import", profile_id=person, broker="mapped", mapping=mapping,
               key="GROWW", files=[zerodha_file("g.csv", APRIL)])
    assert first["files"][0]["added"] == 2
    renamed = ok("ledger_import", profile_id=person, broker="mapped", mapping=mapping,
                 key="GROWWINDIA", files=[zerodha_file("g2.csv", [
                     *APRIL, Row("SYNTHA", "2025-05-02", "sell", "15", "120")])])
    conflicts = renamed["files"][0]["conflicts"]
    assert [c["reason"] for c in conflicts] == ["other_source", "other_source"]
    assert len(renamed["trades"]) == 2


SETTINGS = {
    "fund_classes": {"INF000D01019": "specified"}, "unconfirmed": ["INF000D01019", "NOT-A-FUND"],
    "fmv_2018": {A: "812.35"}, "names": {A: "SYNTHETIC ALPHA LTD", "INE000B01012": " "},
    "brought_forward": [{"origin_year": 2024, "kind": "Short-term capital loss", "amount": "150"}],
    "manual_buys": [{"how": "gift", "for_trade": "S9", "trade": trade_to_json(
        buy("2020-01-02", 5, 100, trade_id="MANUAL:1"))}],
    "excluded": ["S9"],
}


def test_ledger_settings_round_trip_and_profiles(ledger_dir: Path) -> None:
    me = ok("ledger_profile")
    assert me["settings"] == {"fund_classes": {}, "unconfirmed": [], "fmv_2018": {}, "names": {},
                              "brought_forward": [], "manual_buys": [], "excluded": [],
                              "filed_on_time": {}}
    person = me["profile"]["id"]
    ok("ledger_import", profile_id=person, broker="zerodha", files=[zerodha_file("a.csv", APRIL)])
    saved = ok("ledger_save_settings", profile_id=person, settings=SETTINGS)["settings"]
    assert saved["unconfirmed"] == ["INF000D01019"]
    assert saved["names"] == {A: "SYNTHETIC ALPHA LTD"}
    assert saved["excluded"] == []  # S9 isn't a saved sale
    assert saved["manual_buys"][0]["how"] == "gift"
    reopened = ok("ledger_profile")
    assert reopened["settings"] == saved
    other = ok("ledger_profile", name="Second person")
    assert other["settings"]["fmv_2018"] == {}
    assert [p["name"] for p in ok("ledger_profiles")["profiles"]] == ["Me", "Second person"]
    assert [p["name"] for p in other["profiles"]] == ["Me", "Second person"]


@pytest.mark.parametrize(("settings", "message"), [
    ("x", "settings must be an object"),
    ({"manual_buys": [{"trade": {}}]}, "trade is missing"),
    ({"manual_buys": [{"how": "ipo"}]}, "malformed"),
    ({"fund_classes": {"X": "bond"}}, "bond"),
    ({"fmv_2018": {A: "0"}}, "positive"),
])
def test_ledger_settings_are_checked(ledger_dir: Path, settings: object, message: str) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    error = call("ledger_save_settings", profile_id=person, settings=settings)["error"]
    assert message in error["message"]


def test_compute_reports_filing_and_changes(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    trades = [trade_to_json(t)
              for t in (buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 1100))]
    assert ok("compute", year=2025, trades=trades, profile_id=person)["filing"] is None
    assert "filing" not in ok("compute", year=2025, trades=trades)
    filed = ok("ledger_mark_filed", profile_id=person, year=2025, trades=trades, itr_form="ITR-2")
    now = ok("compute", year=2025, trades=trades, profile_id=person)["filing"]
    assert (now["filed_at"], now["itr_form"], now["changes"]) == (filed["filed_at"], "ITR-2", [])
    more = [*trades, trade_to_json(sell("2025-08-01", 1, 1, trade_id="S2"))]
    excluded = ok("compute", year=2025, trades=more, excluded=["S2"], profile_id=person)
    assert excluded["filing"]["changes"] == []  # an excluded sale changes no figure
    late = ok("compute", year=2025, trades=trades, profile_id=person, late_returns=[2024],
              brought_forward=[{"origin_year": 2024, "kind": "Short-term capital loss",
                                "amount": "4000"}])
    assert late["filing"]["changes"] == []
    ok("ledger_unmark_filed", profile_id=person, year=2025)
    assert ok("compute", year=2025, trades=trades, profile_id=person)["filing"] is None


def test_filed_on_time_settings_round_trip(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    saved = ok("ledger_save_settings", profile_id=person,
               settings={"filed_on_time": {"2024": False, "2023": True}})["settings"]
    assert saved["filed_on_time"] == {"2023": True, "2024": False}
    bad = call("ledger_save_settings", profile_id=person, settings={"filed_on_time": {"x": True}})
    assert "malformed" in bad["error"]["message"]


def test_mark_filed_refuses_figures_that_changed_on_screen(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    trades = [trade_to_json(t)
              for t in (buy("2025-05-01", 100, 1000), sell("2025-07-01", 100, 1100))]
    shown = ok("compute", year=2025, trades=trades[:1])
    refused = call("ledger_mark_filed", profile_id=person, year=2025, trades=trades, shown=shown)
    assert "changed while you were looking" in refused["error"]["message"]
    shown = ok("compute", year=2025, trades=trades)
    assert ok("ledger_mark_filed", profile_id=person, year=2025, trades=trades, shown=shown)
    late = ok("compute", year=2025, trades=trades, late_returns=[2025])
    assert late["lapsed"] == [] and late["not_carried"] == []
    bad = call("ledger_mark_filed", profile_id=person, year=2025, trades=trades,
               shown={"summary": {}})
    assert "malformed" in bad["error"]["message"]


def test_ledger_backup_and_restore(ledger_dir: Path) -> None:
    person = ok("ledger_profile", name="Synthetic")["profile"]["id"]
    ok("ledger_import", profile_id=person, broker="zerodha", files=[zerodha_file("a.csv", APRIL)])
    backup = ok("ledger_backup")
    assert backup["name"].startswith("kosh-backup-") and backup["name"].endswith(".kosh")
    ok("ledger_undo", profile_id=person, batch_id=ok("ledger_state", profile_id=person)
       ["batches"][0]["id"])
    assert ok("ledger_state", profile_id=person)["trades"] == []
    restored = ok("ledger_restore", data_base64=backup["data_base64"])
    assert "kosh.sqlite.before-restore-" in restored["before"]
    assert [p["name"] for p in restored["profiles"]] == ["Synthetic"]
    assert len(ok("ledger_state", profile_id=person)["trades"]) == 2
    assert "base64" in call("ledger_restore")["error"]["message"]
    assert "base64" in call("ledger_restore", data_base64="!!")["error"]["message"]
    assert "not an SQLite file" in call("ledger_restore", data_base64=b64("junk" * 300))[
        "error"]["message"]


def test_ledger_restore_size_cap(ledger_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from engine import rpc

    monkeypatch.setattr(rpc, "MAX_IMPORT_BYTES", 10)
    assert "larger than" in call("ledger_restore", data_base64=b64("x" * 11))["error"]["message"]


def test_opening_holdings_from_the_form_and_the_template(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    template = ok("opening_template")
    assert template["name"] == "kosh-opening-holdings.csv" and "how_acquired" in template["csv"]
    row = {"isin": "INE000A01012", "name": "SYNTHETIC ALPHA", "quantity": "10",
           "buy_date": "2016-04-01", "price": "100", "how_acquired": "ipo"}
    added = ok("ledger_add_opening", profile_id=person, rows=[row])
    assert (added["added"], added["names"]) == (1, {"INE000A01012": "SYNTHETIC ALPHA"})
    assert added["batches"][0]["kind"] == "opening"
    again = ok("ledger_add_opening", profile_id=person, rows=[row])
    assert (again["added"], again["duplicates"], again["conflicts"]) == (0, 1, [])
    gift = ok("ledger_add_opening", profile_id=person, rows=[{**row, "how_acquired": "gift"}])
    assert gift["added"] == 0 and gift["conflicts"][0]["reason"] == "same_id"
    assert gift["names"] == {}
    upload = ok("ledger_import", profile_id=person, broker="opening", files=[
        {"name": "o.csv", "data_base64": b64(
            "isin,quantity,buy_date,price\nINE000A01012,5,2017-01-02,90\n")}])
    assert upload["files"][0]["added"] == 1 and upload["batches"][-1]["kind"] == "opening"
    assert len(upload["trades"]) == 2
    assert "rows must be a list" in call("ledger_add_opening", profile_id=person,
                                         rows="x")["error"]["message"]
    assert "one opening-holdings file" in call(
        "import", broker="opening",
        files=[{"name": "a", "data_base64": b64("x")}, {"name": "b", "data_base64": b64("y")}]
    )["error"]["message"]


def test_accounts_and_transfers_over_rpc(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    zerodha = ok("ledger_import", profile_id=person, broker="zerodha",
                 files=[zerodha_file("z.csv", APRIL)])
    assert zerodha["accounts"] == ["Zerodha"] and zerodha["transfers"] == []
    assert {t["account"] for t in zerodha["trades"]} == {"Zerodha"}
    groww = ok("ledger_import", profile_id=person, broker="zerodha", account="Groww",
               files=[zerodha_file("g.csv", [Row("SYNTHA", "2025-05-02", "sell", "15", "120")])])
    assert groww["accounts"] == ["Zerodha", "Groww"]
    isin = zerodha["trades"][0]["instrument"]
    gap = ok("compute", year=2025, trades=groww["trades"])["missing_history"][0]
    assert (gap["account"], gap["quantity"]) == ("Groww", "15")

    too_many = call("ledger_add_transfer", profile_id=person, on="2025-04-10",
                    instrument=isin, quantity="16", from_account="Zerodha", to_account="Groww")
    assert "held 15" in too_many["error"]["message"]
    early = call("ledger_add_transfer", profile_id=person, on="2025-04-02",
                 instrument=isin, quantity="1", from_account="Zerodha", to_account="Groww")
    assert "held 0" in early["error"]["message"]  # the 2-Apr buy isn't in the account yet
    state = ok("ledger_add_transfer", profile_id=person, on="2025-04-10", instrument=isin,
               quantity="15", from_account="Zerodha", to_account="Groww")
    [move] = state["transfers"]
    assert (move["from_account"], move["to_account"], move["quantity"]) == (
        "Zerodha", "Groww", "15")
    report = ok("compute", year=2025, trades=state["trades"], transfers=state["transfers"])
    assert report["complete"] and {line["account"] for line in report["capital_gains"]} == {
        "Groww"}
    assert report["open_lots"] == []

    renamed = ok("ledger_rename_account", profile_id=person, old="Groww", new="Groww India")
    assert renamed["transfers"][0]["to_account"] == "Groww India"
    removed = ok("ledger_remove_transfer", profile_id=person, transfer_id=move["transfer_id"])
    assert removed["transfers"] == []
    assert "TRANSFER:<number>" in call("ledger_remove_transfer", profile_id=person,
                                       transfer_id="7")["error"]["message"]
    assert "both accounts" in call("ledger_add_transfer", profile_id=person, on="2025-04-10",
                                   instrument=isin, quantity="1", from_account="Zerodha",
                                   to_account=" ")["error"]["message"]
    assert "transfers must be a list" in call("compute", year=2025, trades=[],
                                              transfers="x")["error"]["message"]
    assert "transfer is missing" in call("compute", year=2025, trades=[],
                                         transfers=[{"on": "2025-01-01"}])["error"]["message"]


def test_opening_lots_and_assigning_an_account(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    row = {"isin": "INE000A01012", "quantity": "10", "buy_date": "2016-04-01", "price": "100"}
    added = ok("ledger_add_opening", profile_id=person, rows=[row])
    assert added["trades"][0]["account"] is None
    assigned = ok("ledger_assign_account", profile_id=person, account="Zerodha")
    assert assigned["trades"][0]["account"] == "Zerodha"
    in_groww = ok("ledger_add_opening", profile_id=person, account="Groww",
                  rows=[{**row, "buy_date": "2017-04-03"}])
    assert {t["account"] for t in in_groww["trades"]} == {"Zerodha", "Groww"}


def test_transfer_and_assign_details_over_rpc(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    ok("ledger_import", profile_id=person, broker="zerodha", files=[zerodha_file("z.csv", APRIL)])
    again = ok("ledger_import", profile_id=person, broker="zerodha", account="Groww",
               files=[zerodha_file("z.csv", APRIL)])
    assert again["files"][0]["imported_into"] == "Zerodha"
    isin = again["trades"][0]["instrument"]
    unknown = call("ledger_add_transfer", profile_id=person, on="2025-04-10", instrument=isin,
                   quantity="1", from_account="Upstox", to_account="Zerodha")
    assert "no account called 'Upstox'" in unknown["error"]["message"]
    moved = ok("ledger_add_transfer", profile_id=person, on="2025-04-10", instrument=isin,
               quantity="1", from_account="ZERODHA", to_account="groww")
    assert moved["transfers"][0]["from_account"] == "Zerodha"
    renamed = ok("ledger_rename_account", profile_id=person, old="groww", new="Groww")
    assert "settings" in renamed and renamed["transfers"][0]["to_account"] == "Groww"
    row = {"isin": "INE000A01012", "quantity": "10", "buy_date": "2016-04-01", "price": "100"}
    lot = ok("ledger_add_opening", profile_id=person, rows=[row])
    lot_id = next(t["trade_id"] for t in lot["trades"] if t["trade_id"].startswith("OPENING:"))
    assigned = ok("ledger_assign_account", profile_id=person, account="Groww", trade_ids=[lot_id])
    assert next(t["account"] for t in assigned["trades"] if t["trade_id"] == lot_id) == "Groww"
    assert "trade_ids must be" in call("ledger_assign_account", profile_id=person,
                                       account="Groww", trade_ids="x")["error"]["message"]
