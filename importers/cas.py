"""CAMS / KFintech Consolidated Account Statement (CAS) PDF importer for mutual funds.

The PDF itself is parsed by casparser (MIT, https://pypi.org/project/casparser/), which reads
the statement's column layout from character positions and is tested by its maintainers on
real statements. Kosh only maps casparser's typed output to ``Trade`` objects. casparser's
companion ISIN database (casparser-isin) ships an "update" command that downloads over the
network; Kosh never imports it, and tests/test_offline_guard.py fails if parsing pulls in any
network module.

Mapping (unconfirmed against real statements, docs/OPEN_QUESTIONS.md Q-022):
- purchase, SIP, switch-in, dividend reinvestment → BUY at NAV; each stamp-duty row is added
  to the cost of the purchase printed just before it (cost of acquisition, s.72 / s.48);
- redemption, switch-out → SELL at NAV; each STT row is recorded on the sale before it (not
  deductible);
- a reversal (bounced payment) cancels the matching earlier purchase (same units and NAV);
- scheme mergers/consolidations are not transfers — 2025 Act s.70(1)(zj), (zk); 1961 Act
  s.47(xviii), (xix) — and keep the original cost and holding period (2025 Act
  s.2(101)(c)(B)(VII), (IX); 1961 Act s.49(2AD), s.2(42A) Expl. 1(hf)). Kosh can't carry lots
  across schemes yet, so statements with mergers are refused unless ``allow_mergers`` is set,
  in which case they're imported as a sale and a purchase with a warning;
- dividend payouts and TDS are not capital gains; their yearly totals are reported as notes;
- gifts, segregated portfolios, schemes that open with units, a folio listing the same ISIN
  twice, and statements where casparser reports parse problems are refused.
Units are matched FIFO per folio (instrument = ISIN#FOLIO, Q-020).
"""

import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from engine.classify.funds import FundClass, fund_instrument
from engine.dates import tax_year_of
from engine.models import Segment, Side, Trade
from engine.money import ZERO
from importers.base import ImportFormatError, ImportResult

SOURCE = "CAMS / KFintech CAS (PDF)"
BUYS = {"PURCHASE", "PURCHASE_SIP", "SWITCH_IN", "SWITCH_IN_MERGER", "DIVIDEND_REINVEST"}
SELLS = {"REDEMPTION", "SWITCH_OUT", "SWITCH_OUT_MERGER"}
INCOME = {"DIVIDEND_PAYOUT", "TDS_TAX"}
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
    casparser labels overseas funds of funds, gold funds and many hybrids EQUITY, so the user
    must confirm every class (Q-019)."""
    scheme_names: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _Pending:
    index: int
    kind: str
    day: date
    side: Side
    units: Decimal
    nav: Decimal
    extra: Decimal = ZERO
    """Stamp duty (buys) or STT (sells) attached from the following tax rows."""


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


def trades_from_cas(cas: Any, *, name: str = "CAS", allow_mergers: bool = False) -> CasImport:
    """Map a casparser ``CASData`` (CAMS/KFin detailed statement) to trades."""
    if _value(cas, "cas_type") != "DETAILED":
        raise ImportFormatError(
            f"{name}: this is a summary CAS without transactions; download the detailed CAS "
            "(with transaction history) from CAMS or KFintech"
        )
    problems = list(getattr(cas, "parse_warnings", []))
    if problems:
        raise ImportFormatError(
            f"{name}: casparser found problems reading the statement, so gains could be wrong: "
            + "; ".join(problems)
        )
    trades: list[Trade] = []
    warnings: list[str] = list(NOTES)
    classes: dict[str, FundClass] = {}
    names: dict[str, str] = {}
    income: dict[tuple[int, str], Decimal] = defaultdict(lambda: ZERO)
    for folio in cas.folios:
        seen: set[str] = set()
        for scheme in folio.schemes:
            where = f"{name} folio {folio.folio} {scheme.scheme}"
            isin = (scheme.isin or "").strip().upper()
            if not isin:
                raise ImportFormatError(f"{where}: no ISIN found for this scheme")
            if isin in seen:
                raise ImportFormatError(f"{where}: ISIN {isin} appears twice in this folio; "
                                        "check the statement")
            seen.add(isin)
            names[isin] = scheme.scheme
            guess = GUESSED_CLASS.get(str(scheme.type or "").upper())
            if guess is not None and isin not in classes:
                classes[isin] = guess
                warnings.append(f"{where}: fund class guessed as {guess.value} from the scheme "
                                "type; confirm it (Q-019)")
            if Decimal(scheme.open) != 0:
                raise ImportFormatError(
                    f"{where}: statement starts with {scheme.open} units, so their cost is "
                    "unknown; download a CAS covering the whole history (from inception)"
                )
            trades += _scheme_trades(scheme, fund_instrument(isin, str(folio.folio)), where,
                                     warnings, income, allow_mergers)
    for (year, kind), total in sorted(income.items()):
        label = "dividends paid out" if kind == "DIVIDEND_PAYOUT" else "TDS deducted"
        warnings.append(f"{name}: FY {year}-{(year + 1) % 100:02d}: {label} ₹{total} — taxable "
                        "as income from other sources / tax credit, not handled by Kosh")
    trades.sort(key=lambda t: t.trade_date)
    result = ImportResult(SOURCE, tuple(trades), tuple(warnings), format_confirmed=False)
    return CasImport(result, classes, names)


def _scheme_trades(scheme: Any, instrument: str, where: str, warnings: list[str],
                   income: dict[tuple[int, str], Decimal], allow_mergers: bool) -> list[Trade]:
    entries: list[_Pending] = []
    last: dict[tuple[date, Side], _Pending] = {}
    orphans: list[str] = []
    for index, txn in enumerate(scheme.transactions):
        kind = str(_value(txn, "type"))
        day = _as_date(txn.date, where)
        amount = abs(Decimal(txn.amount)) if txn.amount is not None else ZERO
        if kind in {"STAMP_DUTY_TAX", "STT_TAX"}:
            side = Side.BUY if kind == "STAMP_DUTY_TAX" else Side.SELL
            target = last.get((day, side))  # the transaction printed just before it
            if target is None:
                orphans.append(f"{kind.lower()} ₹{amount} on {day}")
            else:
                target.extra += amount
        elif kind in INCOME:
            income[(tax_year_of(day), kind)] += amount
        elif kind in REFUSED:
            raise ImportFormatError(f"{where}: {REFUSED[kind]} on {day} isn't supported yet")
        elif kind in MERGERS and not allow_mergers:
            raise ImportFormatError(
                f"{where}: scheme merger on {day}. A merger isn't a transfer (2025 Act "
                "s.70(1)(zj)); cost and holding period carry over, which Kosh can't do across "
                "schemes yet. Re-import with mergers allowed to treat it as a sale and purchase "
                "(gains will be overstated) (Q-022)"
            )
        elif kind in BUYS | SELLS | {"REVERSAL"}:
            if txn.units is None or Decimal(txn.units) == 0:
                continue
            units = Decimal(txn.units)
            if txn.nav is None:
                raise ImportFormatError(f"{where}: no NAV for the transaction on {day}")
            nav = Decimal(txn.nav)
            if kind == "REVERSAL":
                _cancel_purchase(entries, units, nav, day, where)
                warnings.append(f"{where}: purchase of {-units} units reversed on {day}; "
                                "cancelled")
                continue
            side = Side.BUY if units > 0 else Side.SELL
            if (kind in BUYS) != (side is Side.BUY):
                raise ImportFormatError(f"{where}: {kind} on {day} has units of the wrong sign")
            if kind in MERGERS:
                warnings.append(f"{where}: scheme merger on {day} imported as a sale and "
                                "purchase; a merger isn't a transfer, so the gain is overstated "
                                "(Q-022)")
            entry = _Pending(index, kind, day, side, abs(units), nav)
            entries.append(entry)
            last[(day, side)] = entry
        elif kind != "MISC":
            raise ImportFormatError(f"{where}: unrecognised transaction type {kind} on {day}")
    for orphan in orphans:
        warnings.append(f"{where}: {orphan} had no matching transaction; ignored")

    trades = []
    for entry in entries:
        try:
            trades.append(Trade(
                trade_id=f"CAS:{instrument}:{entry.day.isoformat()}:{entry.index}",
                trade_date=entry.day,
                instrument=instrument,
                side=entry.side,
                quantity=entry.units,
                price=entry.nav,
                charges=entry.extra if entry.side is Side.BUY else ZERO,
                stt=entry.extra if entry.side is Side.SELL else ZERO,
                segment=Segment.MUTUAL_FUND,
            ))
        except ValueError as error:
            raise ImportFormatError(f"{where}: {error}") from None
    return trades


def _cancel_purchase(entries: list[_Pending], units: Decimal, nav: Decimal, day: date,
                     where: str) -> None:
    """A reversal (negative units) undoes the latest earlier purchase of the same units at the
    same NAV, including its stamp duty. Anything else is refused."""
    if units < 0:
        for position in range(len(entries) - 1, -1, -1):
            entry = entries[position]
            if entry.side is Side.BUY and entry.units == -units and entry.nav == nav:
                del entries[position]
                return
    raise ImportFormatError(f"{where}: reversal of {units} units at NAV {nav} on {day} has no "
                            "matching earlier purchase")


def load_cas(data: bytes, *, password: str, name: str = "CAS",
             allow_mergers: bool = False) -> CasImport:
    """Parse a CAS PDF's bytes (usually protected with the PAN or a chosen password)."""
    from casparser import read_cas_pdf  # imported lazily: heavy, and only needed here
    from casparser.exceptions import IncorrectPasswordError

    try:
        cas = read_cas_pdf(io.BytesIO(data), password, output="dict")
    except IncorrectPasswordError:
        raise ImportFormatError(f"{name}: wrong password for the CAS PDF") from None
    except Exception as error:  # casparser, pypdfium2, pydantic and dateutil errors alike
        raise ImportFormatError(
            f"{name}: couldn't read the CAS ({type(error).__name__}: {error})"
        ) from None
    if not hasattr(cas, "folios"):
        raise ImportFormatError(f"{name}: this is a demat (NSDL/CDSL) CAS; import the broker "
                                "tradebook for shares instead")
    return trades_from_cas(cas, name=name, allow_mergers=allow_mergers)
