"""Smoke-test the bundled engine executable through the real stdio protocol.

    uv run python scripts/check_engine_binary.py [path-to-kosh-engine]

Exercises every lazily-imported part (importers, CAS, XLSX decryption, ITR schemas, PDF fonts)
so a missing hidden import or data file fails here, not on a user's machine. Also checks that
casparser-isin's network-capable update CLI is not inside the bundle.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fixtures.xlsx import Num, encrypt, xlsx_bytes  # noqa: E402
from tests.fixtures.zerodha import Row, tradebook_csv  # noqa: E402

PAN = "ABCDE1234F"


def find_binary() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    found = sorted((ROOT / "app" / "src-tauri" / "binaries").glob("kosh-engine-*"))
    if not found:
        sys.exit("no engine binary built; run scripts/build_engine.py first")
    return found[0]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def main() -> None:
    binary = find_binary()
    csv = tradebook_csv([Row("SYNTHA", "2015-01-01", "buy", "10", "100"),
                         Row("SYNTHA", "2025-06-01", "sell", "10", "300")]).encode()
    trades_req = {"broker": "zerodha", "files": [{"name": "tb.csv", "data_base64": b64(csv)}]}
    xlsx = encrypt(xlsx_bytes([["Date", "Type", "Qty", "Price", "Id", "ISIN", "Exchange"],
                               [Num("45778", 1), "BUY", Num("1"), Num("10"), "G1",
                                "INE000A01011", "NSE"]], pad=True), PAN)
    requests = [
        ("version", {}),
        ("import", trades_req),
        ("import", {"broker": "mapped", "password": PAN, "mapping": {
            "trade_date": "Date", "side": "Type", "quantity": "Qty", "price": "Price",
            "trade_id": "Id", "isin": "ISIN", "exchange": "Exchange"},
            "files": [{"name": "g.xlsx", "data_base64": b64(xlsx)}]}),
        ("import", {"broker": "cas", "password": PAN,
                    "files": [{"name": "cas.pdf", "data_base64": b64(b"%PDF-1.4 x")}]}),
    ]
    lines = "".join(json.dumps({"id": i, "method": m, "params": p}) + "\n"
                    for i, (m, p) in enumerate(requests))
    out = subprocess.run([str(binary)], input=lines, capture_output=True, text=True, check=True,
                         timeout=300, encoding="utf-8")
    replies = [json.loads(line) for line in out.stdout.splitlines()]
    assert replies[0]["result"]["version"], replies[0]
    trades = replies[1]["result"]["trades"]
    assert len(trades) == 2, replies[1]
    assert len(replies[2]["result"]["trades"]) == 1, replies[2]  # msoffcrypto + XLSX
    assert "couldn't read the CAS" in replies[3]["error"]["message"], replies[3]  # casparser

    params = {"year": 2025, "trades": trades, "fmv_2018": {"INE000A01011": "150"}}
    more = [("compute", params), ("export_itr", params), ("export_pdf", params)]
    lines = "".join(json.dumps({"id": i, "method": m, "params": p}) + "\n"
                    for i, (m, p) in enumerate(more))
    out = subprocess.run([str(binary)], input=lines, capture_output=True, text=True, check=True,
                         timeout=300, encoding="utf-8")
    compute, itr, pdf = (json.loads(line) for line in out.stdout.splitlines())
    assert compute["result"]["capital_gains"][0]["bucket"] == "LTCG @ 12.5%", compute
    assert itr["result"]["valid"], itr  # bundled CBDT schemas
    assert base64.b64decode(pdf["result"]["pdf_base64"]).startswith(b"%PDF"), pdf  # fonts

    toc = list((ROOT / "build" / "engine" / "work").glob("*/PYZ-00.toc"))
    if toc:
        assert "casparser_isin.cli" not in toc[0].read_text(), "update CLI is in the bundle"
    print(f"ok: {binary.name} ({binary.stat().st_size // (1024 * 1024)} MB)")


if __name__ == "__main__":
    main()
