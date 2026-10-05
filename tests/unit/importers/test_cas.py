from datetime import date
from decimal import Decimal

import pytest

from engine.api import FundClass, compute_tax_year
from engine.models import Segment, Side
from importers import cas as cas_module
from importers.base import ImportFormatError
from importers.cas import load_cas, trades_from_cas
from tests.fixtures.cas import cas, scheme, txn

EQ = "INF000E01011"
DEBT = "INF000D01012"
PAN = "ABCDE1234F"


def _equity_scheme() -> object:
    return scheme("Synthetic Flexi Cap Fund - Direct Growth", EQ, [
        txn("2025-05-02", "PURCHASE", "1000", "100"),
        txn("2025-05-02", "STAMP_DUTY_TAX", None, amount="5"),
        txn("2025-06-02", "PURCHASE_SIP", "500", "110"),
        txn("2025-06-02", "STAMP_DUTY_TAX", None, amount="2.75"),
        txn("2025-07-01", "DIVIDEND_PAYOUT", None, amount="100"),
        txn("2025-10-01", "REDEMPTION", "-1200", "120"),
        txn("2025-10-01", "STT_TAX", None, amount="1.44"),
        txn("2025-10-01", "TDS_TAX", None, amount="0"),
        txn("2025-10-02", "MISC", None, description="***Registration of Nominee***"),
    ])


def test_maps_purchases_redemptions_stamp_duty_and_stt() -> None:
    result = trades_from_cas(cas(("12345/67", [_equity_scheme()])))
    trades = result.result.trades
    assert [(t.side, t.quantity, t.price) for t in trades] == [
        (Side.BUY, Decimal(1000), Decimal(100)), (Side.BUY, Decimal(500), Decimal(110)),
        (Side.SELL, Decimal(1200), Decimal(120))]
    assert trades[0].instrument == f"{EQ}#12345/67"
    assert trades[0].segment is Segment.MUTUAL_FUND
    assert (trades[0].charges, trades[1].charges, trades[2].stt) == (
        Decimal(5), Decimal("2.75"), Decimal("1.44"))
    assert result.suggested_classes == {EQ: FundClass.EQUITY_ORIENTED}
    assert result.scheme_names[EQ].startswith("Synthetic Flexi Cap")
    assert not result.result.format_confirmed
    assert any("Q-022" in w for w in result.result.warnings)


def test_round_trip_to_tax() -> None:
    """FIFO in folio: 1,000 @100 + 5 stamp, then 200 of the 500 @110 (stamp 2.75 → 1.10 on 200).
    Redeem 1,200 @120 = 1,44,000. Cost = 1,00,005 + 22,001.10 = 1,22,006.10.
    STCG 21,993.90 x 20% = 4,398.78 → 4,398 (paise ignored) → 4,400 (nearest ten)."""
    imported = trades_from_cas(cas(("12345/67", [_equity_scheme()])))
    report = compute_tax_year(2025, imported.result.trades,
                              fund_classes=imported.suggested_classes)
    assert report.capital_gains[0].gain + report.capital_gains[1].gain == Decimal("21993.90")
    assert report.special_rate_tax_rounded == Decimal(4400)


def test_debt_guess_switches_and_reversal() -> None:
    debt = scheme("Synthetic Liquid Fund", DEBT, [
        txn("2025-04-10", "SWITCH_IN", "100", "1000"),
        txn("2025-05-10", "PURCHASE", "10", "1001"),
        txn("2025-05-11", "REVERSAL", "-10", "1001"),
        txn("2025-06-10", "SWITCH_OUT", "-100", "1010"),
    ], fund_type="DEBT")
    imported = trades_from_cas(cas(("9", [debt])))
    assert imported.suggested_classes == {DEBT: FundClass.SPECIFIED}
    assert [t.side for t in imported.result.trades] == [Side.BUY, Side.BUY, Side.SELL,
                                                         Side.SELL]
    assert any("reversal of -10 units" in w for w in imported.result.warnings)


def test_merger_warns_and_unknown_type_guess_absent() -> None:
    merged = scheme("Synthetic Old Fund", EQ, [
        txn("2025-04-10", "PURCHASE", "10", "10"),
        txn("2025-08-01", "SWITCH_OUT_MERGER", "-10", "12"),
    ], fund_type=None)
    imported = trades_from_cas(cas(("9", [merged])))
    assert imported.suggested_classes == {}
    assert any("scheme merger" in w for w in imported.result.warnings)


@pytest.mark.parametrize(("schemes", "message"), [
    ([scheme("X", None, [])], "no ISIN"),
    ([scheme("X", EQ, [], open_units="12.5")], "from inception"),
    ([scheme("X", EQ, [txn("2025-04-10", "GIFT_IN", "10", "10")])], "gift received"),
    ([scheme("X", EQ, [txn("2025-04-10", "SEGREGATION", "10", "10")])], "segregated"),
    ([scheme("X", EQ, [txn("2025-04-10", "PURCHASE", "10", None)])], "no NAV"),
    ([scheme("X", EQ, [txn("2025-04-10", "REDEMPTION", "10", "10")])], "wrong sign"),
    ([scheme("X", EQ, [txn("2025-04-10", "PURCHASE", "10", "-1")])], "non-negative"),
])
def test_refused_cases(schemes: list, message: str) -> None:
    with pytest.raises(ImportFormatError, match=message):
        trades_from_cas(cas(("9", schemes)))


def test_summary_cas_refused_and_parse_warnings_kept() -> None:
    with pytest.raises(ImportFormatError, match="summary CAS"):
        trades_from_cas(cas(("9", []), detailed=False))
    imported = trades_from_cas(cas(("9", []), warnings=["page 3 odd"]))
    assert any("casparser: page 3 odd" in w for w in imported.result.warnings)


def test_orphan_stamp_duty_and_zero_units_ignored() -> None:
    imported = trades_from_cas(cas(("9", [scheme("X", EQ, [
        txn("2025-04-10", "STAMP_DUTY_TAX", None, amount="1"),
        txn("2025-04-11", "PURCHASE", "0", "10"),
        txn("2025-04-12", "PURCHASE", "1", "10", description="string date"),
    ])])))
    assert len(imported.result.trades) == 1
    assert any("no matching transaction" in w for w in imported.result.warnings)


def test_string_dates_and_bad_dates() -> None:
    from casparser.enums import TransactionType
    from casparser.types import TransactionData

    good = TransactionData(date="2025-04-12", description="p", amount=Decimal(10),
                           units=Decimal(1), nav=Decimal(10), type=TransactionType.PURCHASE)
    imported = trades_from_cas(cas(("9", [scheme("X", EQ, [good])])))
    assert imported.result.trades[0].trade_date == date(2025, 4, 12)
    bad = TransactionData(date="12/04/2025", description="p", amount=Decimal(10),
                          units=Decimal(1), nav=Decimal(10), type=TransactionType.PURCHASE)
    with pytest.raises(ImportFormatError, match="unreadable date"):
        trades_from_cas(cas(("9", [scheme("X", EQ, [bad])])))


def test_load_cas_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import casparser
    from casparser.exceptions import CASParseError, IncorrectPasswordError

    def raising(error: Exception):  # type: ignore[no-untyped-def]
        def read(*_: object, **__: object) -> object:
            raise error
        return read

    monkeypatch.setattr(casparser, "read_cas_pdf", raising(IncorrectPasswordError("x")))
    with pytest.raises(ImportFormatError, match="wrong password"):
        load_cas(b"%PDF", password=PAN)
    monkeypatch.setattr(casparser, "read_cas_pdf", raising(CASParseError("bad")))
    with pytest.raises(ImportFormatError, match="couldn't read the CAS"):
        load_cas(b"%PDF", password=PAN)
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *_, **__: object())
    with pytest.raises(ImportFormatError, match="demat"):
        load_cas(b"%PDF", password=PAN)
    monkeypatch.setattr(casparser, "read_cas_pdf",
                        lambda *_, **__: cas(("9", [_equity_scheme()])))
    assert len(load_cas(b"%PDF", password=PAN).result.trades) == 3
    assert cas_module.SOURCE.startswith("CAMS")


def test_real_pdf_parse_rejects_garbage() -> None:
    """Goes through casparser for real: a non-PDF must become a clean ImportFormatError."""
    with pytest.raises(ImportFormatError):
        load_cas(b"not a pdf at all", password=PAN)


def test_future_transaction_type_is_warned_not_dropped_silently() -> None:
    from types import SimpleNamespace

    novel = SimpleNamespace(type="BONUS_UNITS", date=date(2025, 4, 10), amount=None,
                            units=Decimal(5), nav=Decimal(10))
    folio = cas(("9", [scheme("X", EQ, [])]))
    folio.folios[0].schemes[0].transactions.append(novel)  # type: ignore[arg-type]
    result = trades_from_cas(folio)
    assert any("BONUS_UNITS on 2025-04-10 ignored" in w for w in result.result.warnings)
