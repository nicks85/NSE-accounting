"""CAMS / KFintech Consolidated Account Statement (CAS) PDF importer for mutual funds.

The PDF itself is parsed by casparser (MIT, https://pypi.org/project/casparser/), which reads
the statement's column layout from character positions and is tested by its maintainers on
real statements. Kosh only maps casparser's typed output to ``Trade`` objects. casparser's
companion ISIN database (casparser-isin) ships an "update" command that downloads over the
network; Kosh never imports it, and tests/test_offline_guard.py fails if parsing pulls in any
network module.

Mapping (unconfirmed against real statements, docs/OPEN_QUESTIONS.md Q-022):
- purchase, SIP, switch-in, dividend reinvestment → BUY at NAV; stamp duty on the same day is
  added to the purchase cost (cost of acquisition, s.72 / s.48);
- redemption, switch-out → SELL at NAV; STT on the same day is recorded (not deductible);
- scheme mergers (switch in/out "merger") are not transfers (2025 Act s.70; 1961 Act
  s.47(xix)), so cost and holding period should carry over; they are imported as a sale and a
  purchase with a warning until merger handling is built;
- dividend payouts, TDS and informational rows are ignored (not capital gains);
- gifts and segregated portfolios are refused (previous owner's cost / special cost rules).
Units are matched FIFO per folio (instrument = ISIN#FOLIO, Q-020).
"""

import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from engine.classify.funds import FundClass, fund_instrument
from engine.models import Segment, Side, Trade
from engine.money import ZERO
from importers.base import ImportFormatError, ImportResult

SOURCE = "CAMS / KFintech CAS (PDF)"
BUYS = {"PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "SWITCH_IN_MERGER", "DIVIDEND_REINVEST"}
SELLS = {"REDEMPTION", "SWITCH_OUT", "SWITCH_OUT_MERGER"}
IGNORED = {"DIVIDEND_PAYOUT", "TDS_TAX", "MISC"}
REFUSED = {"GIFT_IN": "gift received (cost is the previous owner's)",
           "GIFT_OUT": "gift given", "SEGREGATION": "segregated portfolio"}
MERGERS = {"SWITCH_IN_MERGER", "SWITCH_OUT_MERGER"}
GUESSED_CLASS = {"EQUITY": FundClass.EQUITY_ORIENTED, "DEBT": FundClass.SPECIFIED}

NOTES = (
    "CAS transactions were mapped to trades by Kosh; the mapping is unconfirmed against real "
    "statements (docs/OPEN_QUESTIONS.md Q-022). Check units and costs against the CAS.",
)


@dataclass(frozen=True, slots=True)
class CasImport:
    result: ImportResult
    suggested_classes: dict[str, FundClass] = field(default_factory=dict)
    """Fund class guessed from casparser's scheme type (EQUITY / DEBT), per ISIN. A guess:
    the user must confirm it (Q-019)."""
    scheme_names: dict[str, str] = field(default_factory=dict)


def _value(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    return getattr(value, "value", value)  # enums → their string value


def _as_date(value: date | str, where: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ImportFormatError(f"{where}: unreadable date {value!r}") from None


def trades_from_cas(cas: Any, *, name: str = "CAS") -> CasImport:
    """Map a casparser ``CASData`` (CAMS/KFin detailed statement) to trades."""
    if _value(cas, "cas_type") != "DETAILED":
        raise ImportFormatError(
            f"{name}: this is a summary CAS without transactions; download the detailed CAS "
            "(with transaction history) from CAMS or KFintech"
        )
    trades: list[Trade] = []
    warnings: list[str] = list(NOTES)
    warnings += [f"{name}: casparser: {w}" for w in getattr(cas, "parse_warnings", [])]
    classes: dict[str, FundClass] = {}
    names: dict[str, str] = {}
    for folio in cas.folios:
        for scheme in folio.schemes:
            where = f"{name} folio {folio.folio} {scheme.scheme}"
            isin = (scheme.isin or "").strip().upper()
            if not isin:
                raise ImportFormatError(f"{where}: no ISIN found for this scheme")
            names[isin] = scheme.scheme
            guess = GUESSED_CLASS.get(str(scheme.type or "").upper())
            if guess is not None:
                classes.setdefault(isin, guess)
            if Decimal(scheme.open) != 0:
                raise ImportFormatError(
                    f"{where}: statement starts with {scheme.open} units, so their cost is "
                    "unknown; download a CAS covering the whole history (from inception)"
                )
            trades += _scheme_trades(scheme, fund_instrument(isin, str(folio.folio)), where,
                                     warnings)
    trades.sort(key=lambda t: t.trade_date)
    result = ImportResult(SOURCE, tuple(trades), tuple(warnings), format_confirmed=False)
    return CasImport(result, classes, names)


def _scheme_trades(scheme: Any, instrument: str, where: str, warnings: list[str]) -> list[Trade]:
    stamp: dict[date, Decimal] = defaultdict(lambda: ZERO)
    stt: dict[date, Decimal] = defaultdict(lambda: ZERO)
    rows = []
    for index, txn in enumerate(scheme.transactions):
        kind = str(_value(txn, "type"))
        day = _as_date(txn.date, where)
        amount = abs(Decimal(txn.amount)) if txn.amount is not None else ZERO
        if kind == "STAMP_DUTY_TAX":
            stamp[day] += amount
        elif kind == "STT_TAX":
            stt[day] += amount
        elif kind in REFUSED:
            raise ImportFormatError(f"{where}: {REFUSED[kind]} on {day} isn't supported yet")
        elif kind in BUYS | SELLS | {"REVERSAL", "UNKNOWN"}:
            rows.append((index, kind, day, txn))
        elif kind not in IGNORED:
            warnings.append(f"{where}: transaction type {kind} on {day} ignored")

    trades = []
    stamp_left, stt_left = dict(stamp), dict(stt)
    for index, kind, day, txn in rows:
        if txn.units is None or Decimal(txn.units) == 0:
            continue
        units = Decimal(txn.units)
        if kind in {"REVERSAL", "UNKNOWN"}:
            warnings.append(f"{where}: {kind.lower()} of {units} units on {day} imported as a "
                            f"{'purchase' if units > 0 else 'redemption'} at NAV")
        if kind in MERGERS:
            warnings.append(f"{where}: scheme merger on {day} imported as a sale and purchase; "
                            "a merger isn't a transfer, so check the gain (Q-022)")
        if txn.nav is None:
            raise ImportFormatError(f"{where}: no NAV for the transaction on {day}")
        side = Side.BUY if units > 0 else Side.SELL
        if (kind in BUYS and side is Side.SELL) or (kind in SELLS and side is Side.BUY):
            raise ImportFormatError(f"{where}: {kind} on {day} has units of the wrong sign")
        charges = stamp_left.pop(day, ZERO) if side is Side.BUY else ZERO
        stt_paid = stt_left.pop(day, ZERO) if side is Side.SELL else ZERO
        try:
            trades.append(Trade(
                trade_id=f"CAS:{instrument}:{day.isoformat()}:{index}",
                trade_date=day,
                instrument=instrument,
                side=side,
                quantity=abs(units),
                price=Decimal(txn.nav),
                charges=charges,
                stt=stt_paid,
                segment=Segment.MUTUAL_FUND,
            ))
        except ValueError as error:
            raise ImportFormatError(f"{where}: {error}") from None
    for day in (*stamp_left, *stt_left):
        warnings.append(f"{where}: stamp duty or STT on {day} had no matching transaction; "
                        "ignored")
    return trades


def load_cas(data: bytes, *, password: str, name: str = "CAS") -> CasImport:
    """Parse a CAS PDF's bytes (usually protected with the PAN or a chosen password)."""
    from casparser import read_cas_pdf  # imported lazily: heavy, and only needed here
    from casparser.exceptions import CASParseError, IncorrectPasswordError

    try:
        cas = read_cas_pdf(io.BytesIO(data), password, output="dict")
    except IncorrectPasswordError:
        raise ImportFormatError(f"{name}: wrong password for the CAS PDF") from None
    except CASParseError as error:
        raise ImportFormatError(f"{name}: couldn't read the CAS ({error})") from None
    if not hasattr(cas, "folios"):
        raise ImportFormatError(f"{name}: this is a demat (NSDL/CDSL) CAS; import the broker "
                                "tradebook for shares instead")
    return trades_from_cas(cas, name=name)
