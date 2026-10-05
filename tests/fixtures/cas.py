"""Synthetic casparser ``CASData`` builders. Fake names, folios and ISINs only; the PAN, if
any, is the repo's synthetic ABCDE1234F."""

from datetime import date
from decimal import Decimal

from casparser.enums import CASFileType, FileType, TransactionType
from casparser.types import (
    CASData,
    Folio,
    InvestorInfo,
    Scheme,
    SchemeValuation,
    StatementPeriod,
    TransactionData,
)


def txn(day: str, kind: str, units: str | None, nav: str | None = None,
        amount: str | None = None, description: str = "") -> TransactionData:
    units_d = Decimal(units) if units is not None else None
    nav_d = Decimal(nav) if nav is not None else None
    if amount is None and units_d is not None and nav_d is not None:
        amount = str(units_d * nav_d)
    return TransactionData(
        date=date.fromisoformat(day), description=description or kind.title(),
        amount=Decimal(amount) if amount is not None else None, units=units_d, nav=nav_d,
        balance=None, type=TransactionType[kind])


def scheme(name: str, isin: str | None, transactions: list[TransactionData], *,
           open_units: str = "0", fund_type: str | None = "EQUITY") -> Scheme:
    return Scheme(
        scheme=name, rta_code="X1", rta="CAMS", type=fund_type, isin=isin, amfi="100001",
        open=Decimal(open_units), close=Decimal(0), close_calculated=Decimal(0),
        valuation=SchemeValuation(date=date(2026, 3, 31), nav=Decimal(1), value=Decimal(0)),
        transactions=transactions)


def cas(*folios: tuple[str, list[Scheme]], detailed: bool = True,
        warnings: list[str] | None = None) -> CASData:
    return CASData(
        statement_period=StatementPeriod(**{"from": "01-Jan-2015", "to": "31-Mar-2026"}),
        folios=[Folio(folio=number, amc="Synthetic AMC", PAN="ABCDE1234F", schemes=schemes)
                for number, schemes in folios],
        investor_info=InvestorInfo(name="Synthetic Investor", email="x@example.invalid",
                                   address="Nowhere", mobile="0000000000"),
        cas_type=CASFileType.DETAILED if detailed else CASFileType.SUMMARY,
        file_type=FileType.CAMS,
        parse_warnings=warnings or [])
