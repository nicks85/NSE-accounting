"""QA review of brief 0003 (FIFO per demat account): edge cases. Synthetic data only.
Z = "Zerodha", G = "Groww". Year 2025 = FY 2025-26. The last five were open defects, now fixed."""

import base64
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

from engine.api import compute_tax_year, held_on
from engine.ledger import Batch, Ledger
from engine.ledger.migrations import V1, V2
from engine.ledger.store import _statements
from engine.matching.corporate_actions import Bonus, Split
from engine.models import Trade, Transfer
from engine.rpc import handle
from tests.fixtures.zerodha import Row, tradebook_csv
from tests.golden.helpers import A, buy, d, fut, sell

Z, G = "Zerodha", "Groww"


def at(account: str, trade: Trade) -> Trade:
    return replace(trade, account=account)


def ok(method: str, **params: object) -> dict:  # type: ignore[type-arg]
    response = handle({"id": 1, "method": method, "params": params})
    assert "error" not in response, response
    return response["result"]  # type: ignore[no-any-return]


# -- tax behaviour that is correct today (regression guards) ----------------------------------


def test_r1_split_before_a_transfer_moves_post_split_shares_with_their_factor() -> None:
    """Z buys 10 @1,000 on 2-Jan-17 (before 31-Jan-18). 1:5 split ex 2-Jan-24 → 50 shares,
    split_factor 5. 50 moved Z→G on 2-Feb-24. G sells 50 @300 on 2-Jun-25: LTCG, cost 10,000,
    sale 15,000; the moved lot keeps factor 5 for grandfathering."""
    move = Transfer("T", date(2024, 2, 2), A, d(50), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2017-01-02", 10, 1000)),
                                at(G, sell("2025-06-02", 50, 300))],
                         actions=[Split(A, date(2024, 1, 2), 1, 5)], transfers=[move])
    [line] = r.capital_gains
    assert (line.disposal.account, line.disposal.quantity, line.disposal.split_factor,
            line.disposal.acquired_on, line.bucket.label) == (
        G, d(50), d(5), date(2017, 1, 2), "LTCG @ 12.5%")
    assert line.disposal.cost == d(10000)
    assert r.complete


def test_r2_transfer_on_a_sale_day_runs_before_the_sale() -> None:
    """Transfer Z→G and G's sale on the same day: the transfer comes first, so G's sale is
    covered (documented order: actions, transfers, trades)."""
    move = Transfer("T", date(2025, 6, 2), A, d(10), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2024-01-02", 10, 100)),
                                at(G, sell("2025-06-02", 10, 150))], transfers=[move])
    assert r.complete and r.capital_gains[0].disposal.account == G


def test_r3_transfer_before_the_first_trade_moves_nothing_and_says_so() -> None:
    move = Transfer("T", date(2023, 1, 1), A, d(10), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2024-01-02", 10, 100))], transfers=[move])
    assert [(lot.account, lot.quantity) for lot in r.open_lots] == [(Z, d(10))]
    assert any(n.code == "TRANSFER_SHORT" for n in r.warnings)


def test_r4_fno_positions_are_matched_per_account() -> None:
    """Long 50 futures at Z and short 50 at G: two open positions, not a closed one."""
    r = compute_tax_year(2025, [at(Z, fut("BUY", "2025-06-02", 50, 100)),
                                at(G, fut("SELL", "2025-06-03", 50, 110))])
    assert r.business.lines == ()


def test_r5_held_on_counts_a_split_only_when_given_the_actions() -> None:
    """held_on without actions under-counts after a split. The RPC passes none; the app
    sends no actions either, so today the two agree; they must stay in step."""
    trades = [at(Z, buy("2017-01-02", 10, 1000))]
    split = Split(A, date(2024, 1, 2), 1, 5)
    assert held_on(trades, [], Z, A, date(2024, 2, 2), actions=[split]) == d(50)
    assert held_on(trades, [], Z, A, date(2024, 2, 2)) == d(10)


# -- defects found in review, now fixed --------------------------------------------------------


def test_r6_bonus_stripping_sees_bonus_shares_held_in_another_account() -> None:
    """Z buys 100 @1,000 on 1-May-25; 1:1 bonus ex/record 15-Jun-25, allotted 17-Jun-25 (100
    bonus shares in Z). On 20-Jun-25 the 100 originals (oldest entry) move Z→G. G sells them
    @500 on 1-Jul-25: loss 50,000. The person still holds the 100 bonus shares (in Z), so
    1961 s.94(8) / 2025 s.175(9) ignore the loss and add it to the bonus shares' cost."""
    bonus = Bonus(A, date(2025, 6, 15), held=1, bonus=1, allotment_date=date(2025, 6, 17))
    move = Transfer("T", date(2025, 6, 20), A, d(100), Z, G)
    r = compute_tax_year(2025, [at(Z, buy("2025-05-01", 100, 1000)),
                                at(G, sell("2025-07-01", 100, 500))],
                         actions=[bonus], transfers=[move])
    [line] = r.capital_gains
    assert line.disposal.stripped_loss == d(50000)
    [bonus_lot] = r.open_lots
    assert (bonus_lot.account, bonus_lot.cost) == (Z, d(50000))


def test_r7_add_transfer_matches_the_account_name_ignoring_case(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    data = base64.b64encode(tradebook_csv(
        [Row("SYNTHA", "2025-04-02", "buy", "10", "100")]).encode()).decode()
    state = ok("ledger_import", profile_id=person, broker="zerodha",
               files=[{"name": "z.csv", "data_base64": data}])
    isin = state["trades"][0]["instrument"]
    ok("ledger_add_transfer", profile_id=person, on="2025-04-10", instrument=isin,
       quantity="10", from_account="zerodha", to_account="Groww")


def test_r8_same_opening_lot_in_two_accounts_is_two_lots(ledger_dir: Path) -> None:
    """One 2016 purchase of 100, half later moved: 50 in Z and 50 in G, entered one account
    at a time. Both must be kept."""
    person = ok("ledger_profile")["profile"]["id"]
    row = {"isin": "INE000A01012", "quantity": "50", "buy_date": "2016-04-01", "price": "100"}
    ok("ledger_add_opening", profile_id=person, account=Z, rows=[row])
    second = ok("ledger_add_opening", profile_id=person, account=G,
                rows=[{**row, "how_acquired": "transfer", "entered_on": "2020-01-02"}])
    assert second["added"] == 1


def _v2_with_mapped_broker(path: Path, key: str) -> None:
    with sqlite3.connect(path) as db:
        for sql in (V1, V2):
            for statement in _statements(sql):
                db.execute(statement)
        db.execute("INSERT INTO meta VALUES ('schema_version', '2')")
        db.execute("INSERT INTO profile VALUES (1, 'Old', '2026-10-01')")
        db.execute("INSERT INTO instrument (id, isin, kind) VALUES (1, ?, 'EQUITY')", (A,))
        db.execute("INSERT INTO import_batch (id, profile_id, kind, broker, imported_at)"
                   " VALUES (1, 1, 'tradebook', ?, '2026-10-01')", (key,))
        db.execute("INSERT INTO trade (profile_id, batch_id, instrument_id, source_id,"
                   " segment, trade_date, side, quantity, price, dedupe_key) VALUES"
                   " (1, 1, 1, 'M:1', 'EQUITY', '2024-01-02', 'BUY', '10', '100', 'k1')")
    db.close()


def test_r9_upgraded_mapped_account_is_reused_by_the_next_import(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite"
    _v2_with_mapped_broker(path, "ICICIDIRECT")
    with Ledger.open(path) as ledger:
        ledger.import_trades(1, Batch(broker="ICICIDIRECT", file_sha256="x"),
                             [sell("2025-06-02", 10, 150, trade_id="M:2")],
                             account="ICICI Direct")  # ImportScreen defaultAccount for mapped
        assert len(ledger.accounts(1)) == 1
        assert ledger.compute(1, [2025])[0].complete


def test_r10_moved_in_lot_cant_cover_a_sale_before_it_arrived() -> None:
    lot = at(G, replace(buy("2023-01-02", 10, 100, trade_id="OPENING:x:1"),
                        entered_on=date(2024, 9, 2)))
    r = compute_tax_year(2024, [lot, at(G, sell("2024-06-03", 10, 150))])
    assert not r.complete


# -- re-review of the fixes ---------------------------------------------------------------------

OPENING_ROW = {"isin": "INE000A01012", "quantity": "50", "buy_date": "2016-04-01",
               "price": "100"}


def test_r11_same_lot_typed_with_other_case_is_still_a_duplicate(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    ok("ledger_add_opening", profile_id=person, account=Z, rows=[OPENING_ROW])
    again = ok("ledger_add_opening", profile_id=person, account="zerodha", rows=[OPENING_ROW])
    assert (again["added"], again["duplicates"]) == (0, 1)


def test_r12_same_lot_after_a_rename_is_still_a_duplicate(ledger_dir: Path) -> None:
    person = ok("ledger_profile")["profile"]["id"]
    ok("ledger_add_opening", profile_id=person, account=Z, rows=[OPENING_ROW])
    ok("ledger_rename_account", profile_id=person, old=Z, new="Zerodha main")
    again = ok("ledger_add_opening", profile_id=person, account="Zerodha main",
               rows=[OPENING_ROW])
    assert (again["added"], again["duplicates"]) == (0, 1)


def test_r13_assigning_a_lot_identical_to_one_already_there(ledger_dir: Path) -> None:
    """The same holding, once without an account and once in Z: putting the first in Z would
    count it twice, so it's refused in plain words and nothing changes."""
    person = ok("ledger_profile")["profile"]["id"]
    ok("ledger_add_opening", profile_id=person, rows=[OPENING_ROW])  # no account yet
    ok("ledger_add_opening", profile_id=person, account=Z, rows=[OPENING_ROW])
    refused = handle({"id": 1, "method": "ledger_assign_account",
                      "params": {"profile_id": person, "account": Z}})
    assert refused["error"]["type"] == "LedgerError"
    assert "would count it twice" in refused["error"]["message"]
    state = ok("ledger_state", profile_id=person)
    assert sorted(str(t["account"]) for t in state["trades"]) == ["None", Z]

