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
