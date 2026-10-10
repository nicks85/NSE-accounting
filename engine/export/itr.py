"""Fill the capital-gains schedules of the official ITR-2 / ITR-3 JSON (AY 2026-27).

Kosh computes only capital gains and trading income, so it exports the schedules it can fill —
Schedule 112A and Schedule CG — not a complete return. Each schedule starts from a skeleton
derived from the official CBDT schema (engine/export/skeleton.py), Kosh's figures are overlaid,
and the result is validated against the schema before it is returned.

Amounts are whole rupees (half-up), rounded per line; totals are sums of the rounded lines so
the schedule adds up. Line placement follows the forms:
- STCG on STT-paid equity / equity-oriented units (s.111A) → Schedule CG A2 (``EquityMFonSTT``,
  section code 1A) — the "20%" column;
- other short-term gains (non-equity fund units) → A5 (``SaleOnOtherAssets``) — "applicable
  rate";
- LTCG under s.112A → Schedule 112A, carried to CG B (``SaleOfEquityShareUs112A``);
- other LTCG (s.112, non-equity fund units) → CG B (``SaleofAssetNADtls``);
- both LTCG kinds fall in the form's single "12.5%" column.
Assumptions are logged as docs/OPEN_QUESTIONS.md Q-025.
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from engine.api import TaxYearReport
from engine.classify.capital_gains import Bucket, CapitalGainLine, Regime, Term
from engine.classify.funds import isin_of
from engine.export.json_out import to_json
from engine.export.schemas_registry import SchemaError, load_schema, validate
from engine.export.skeleton import skeleton
from engine.money import ZERO
from engine.notices import Notice
from engine.rules.rounding import round_rupee

GRANDFATHERING_CUTOFF = date(2018, 1, 31)
"""Schedule 112A: "BE" = acquired on or before 31-Jan-2018."""
CONSOLIDATED_ISIN = "INNOTREQUIRD"
CONSOLIDATED_NAME = "CONSOLIDATED"
NAME_LIMIT = 125

CATEGORIES = ("20Per", "AppRate", "12_5Per")
"""Form columns Kosh fills: STCG 20% (s.111A), STCG at applicable (slab) rate, LTCG 12.5%."""
SET_OFF_ORDER = ("AppRate", "20Per", "12_5Per")
"""Order in which a category's loss is set off against other categories' gains: slab rate
first, then 20%, then 12.5% (the same priority as the engine, Q-008)."""
LOSS_FIELD = {"20Per": "StclSetoff20Per", "AppRate": "StclSetoffAppRate",
              "12_5Per": "LtclSetOff12_5Per"}
PERIODS = ("Upto15Of6", "Upto15Of9", "Up16Of9To15Of12", "Up16Of12To15Of3", "Up16Of3To31Of3")
ACCRUAL_FIELD = {"20Per": "ShortTermUnder20Per", "AppRate": "ShortTermUnderAppRate",
                 "12_5Per": "LongTermUnder12_5Per"}


class ExportError(ValueError):
    """The report can't be exported (unsupported year, or a gain type the form can't hold)."""


def incomplete_message(report: TaxYearReport) -> str:
    """Why an incomplete year can't be exported (shared by the JSON and PDF exports)."""
    count = len(report.missing_history)
    return (f"{count} sale(s) are missing purchase history; add each purchase or exclude the "
            "sale on the Gains screen before exporting")


@dataclass(frozen=True, slots=True)
class ItrExport:
    form: str
    start_year: int
    schedules: dict[str, dict[str, Any]]
    warnings: tuple[Notice, ...] = ()
    errors: tuple[SchemaError, ...] = field(default=())
    """Schema validation errors; empty when every schedule validates."""

    @property
    def valid(self) -> bool:
        return not self.errors


def choose_form(report: TaxYearReport) -> str:
    """ITR-3 when there is business income (intraday or F&O), else ITR-2."""
    return "ITR-3" if report.business.lines else "ITR-2"


def _category(bucket: Bucket) -> str:
    if bucket.term is Term.SHORT and bucket.rate is None:
        return "AppRate"
    if bucket.term is Term.SHORT and bucket.rate == Decimal("0.20"):
        return "20Per"
    if bucket.term is Term.LONG and bucket.rate == Decimal("0.125"):
        return "12_5Per"
    raise ExportError(f"{bucket.label} has no column in the AY 2026-27 schedule")


def _period(day: date) -> str:
    month_day = (day.month, day.day)
    if (4, 1) <= month_day <= (6, 15):
        return PERIODS[0]
    if (6, 16) <= month_day <= (9, 15):
        return PERIODS[1]
    if (9, 16) <= month_day <= (12, 15):
        return PERIODS[2]
    if month_day >= (12, 16) or month_day <= (3, 15):
        return PERIODS[3]
    return PERIODS[4]


def _rupees(amount: Decimal) -> int:
    return int(round_rupee(amount))


def _deductions(cost: int, expenses: int) -> dict[str, int]:
    return {"AquisitCost": cost, "ImproveCost": 0, "ExpOnTrans": expenses,
            "TotalDedn": cost + expenses}


def _sale_block(base: dict[str, Any], lines: Iterable[CapitalGainLine],
                other_assets: bool) -> dict[str, Any]:
    """Fill a full-value / deductions / balance block from lines (rupees per line)."""
    sale = cost = expenses = stripped = 0
    for line in lines:
        sale += _rupees(line.disposal.sale_value)
        cost += _rupees(line.cost)
        expenses += _rupees(line.disposal.transfer_expenses)
        stripped += _rupees(line.disposal.stripped_loss)
    block = dict(base)
    if other_assets:
        block["FullValueConsdOthUnqshr"] = sale
    block["FullConsideration"] = sale
    block["DeductSec48"] = _deductions(cost, expenses)
    block["BalanceCG"] = sale - cost - expenses
    if "LossSec94of7Or94of8" in block:
        block["LossSec94of7Or94of8"] = stripped  # bonus-stripping loss disallowed
        block["CapgainonAssets"] = block["BalanceCG"] + stripped
    else:
        block["CapgainonAssets"] = block["BalanceCG"]
    return block


ISIN_PATTERN = re.compile(r"IN[0-9A-Z]{10}")
PER_UNIT = Decimal("0.0001")
"""Schema precision for per-share figures (multipleOf 0.0001)."""


def _lower_column(form: str, start_year: int) -> str:
    """Schedule 112A column 9 ("lower of 6 and 11") is named differently in ITR-2 and ITR-3."""
    columns = load_schema(form, start_year)["definitions"]["Schedule112A115ADType"]["properties"]
    return "LTCGBeforelower6and11" if "LTCGBeforelower6and11" in columns else "LTCGBeforelowerB1B2"


def _row_112a(when: str, isin: str, name: str, lines: list[CapitalGainLine],
              lower_column: str) -> dict[str, Any]:
    """One Schedule 112A row whose columns satisfy the form's own arithmetic on whole rupees:
    7 = higher of 8 and 9, 9 = lower of 6 and 11, 13 = 7 + 12, 14 = 6 - 13."""
    sale = sum(_rupees(line.disposal.sale_value) for line in lines)
    actual = sum(_rupees(line.disposal.cost) for line in lines)
    expenses = sum(_rupees(line.disposal.transfer_expenses) for line in lines)
    quantity = sum((line.disposal.quantity for line in lines), ZERO)
    fmv = sum(_rupees(line.grandfathered_fmv or ZERO) for line in lines)
    lower = min(sale, fmv) if when == "BE" else 0
    cost = max(actual, lower)
    row: dict[str, Any] = {"ShareOnOrBefore": when, "ISINCode": isin, "ShareUnitName": name}
    if when == "BE":  # quantity and per-share prices are meaningless on a consolidated row
        row["NumSharesUnits"] = quantity.quantize(PER_UNIT)
        row["SalePricePerShareUnit"] = (Decimal(sale) / quantity).quantize(PER_UNIT)
    row.update({
        "TotSaleValue": sale,
        "CostAcqWithoutIndx": cost,
        "AcquisitionCost": Decimal(actual),
        lower_column: lower,
        "FairMktValuePerShareunit": (Decimal(fmv) / quantity).quantize(PER_UNIT)
        if when == "BE" else Decimal(0),
        "TotFairMktValueCapAst": fmv,
        "ExpExclCnctTransfer": Decimal(expenses),
        "TotalDeductions": cost + expenses,
        "Balance": sale - cost - expenses,
    })
    return row


def schedule_112a(report: TaxYearReport, form: str,
                  names: Mapping[str, str] | None = None) -> tuple[dict[str, Any], list[Notice]]:
    """Rows: one per lot acquired on or before 31-Jan-2018 ("BE", so each row's grandfathering
    arithmetic holds), and one CONSOLIDATED row for everything acquired later ("AE")."""
    names = names or {}
    notices: list[Notice] = []
    lower_column = _lower_column(form, report.pack.start_year)
    before: list[CapitalGainLine] = []
    after: list[CapitalGainLine] = []
    for line in report.capital_gains:
        if line.manual or not line.bucket.exemption_eligible:
            continue
        if line.disposal.acquired_on > GRANDFATHERING_CUTOFF:
            after.append(line)  # one consolidated row, no ISIN needed
            continue
        if not ISIN_PATTERN.fullmatch(isin_of(line.disposal.instrument)):
            raise ExportError(
                "Schedule 112A needs the ISIN of shares bought on or before 31-Jan-2018; "
                f"{line.disposal.instrument!r} (bought {line.disposal.acquired_on}) has none")
        before.append(line)

    rows: list[dict[str, Any]] = []
    unnamed: set[str] = set()
    for line in sorted(before, key=lambda ln: (isin_of(ln.disposal.instrument),
                                               ln.disposal.acquired_on)):
        isin = isin_of(line.disposal.instrument)
        if isin not in names:
            unnamed.add(isin)
        if line.grandfathered_fmv is None:
            notices.append(Notice(
                "EXPORT", f"Schedule 112A: {isin} acquired {line.disposal.acquired_on} has no "
                "31-Jan-2018 FMV, so the FMV columns are 0 and the actual cost was used",
                ref=line.disposal.close_trade_id))
        rows.append(_row_112a("BE", isin, names.get(isin, isin)[:NAME_LIMIT], [line],
                              lower_column))
    if after:
        rows.append(_row_112a("AE", CONSOLIDATED_ISIN, CONSOLIDATED_NAME, after, lower_column))
    notices += [Notice("EXPORT", f"Schedule 112A: no name for {isin}; the ISIN was used as the "
                       "share/unit name") for isin in sorted(unnamed)]

    schedule = skeleton(form, report.pack.start_year, "Schedule112A")
    schedule["Schedule112ADtls"] = rows
    totals = {
        "SaleValue112A": "TotSaleValue", "CostAcqWithoutIndx112A": "CostAcqWithoutIndx",
        "AcquisitionCost112A": "AcquisitionCost", "LTCGBeforelowerB1B2112A": lower_column,
        "FairMktValueCapAst112A": "TotFairMktValueCapAst",
        "ExpExclCnctTransfer112A": "ExpExclCnctTransfer", "Deductions112A": "TotalDeductions",
        "Balance112A": "Balance",
    }
    for total, column in totals.items():
        schedule[total] = int(sum((Decimal(row[column]) for row in rows), ZERO))
    if "TotalBalance112A" in schedule:  # ITR-2 only
        schedule["TotalBalance112A"] = schedule["Balance112A"]
    return schedule, notices


def _row_key(column: str) -> str:
    return "InLtcg12_5Per" if column == "12_5Per" else f"InStcg{column}"


def _set_off_table(base: dict[str, Any], nets: dict[str, int]) -> dict[str, Any]:
    """Schedule CG "set-off of current year capital losses": net figure per column; a negative
    column's loss is set off against positive columns (short-term losses against any column,
    the 12.5% LTCL only against LTCG)."""
    table = {key: (dict(value) if isinstance(value, dict) else value)
             for key, value in base.items()}
    gains = {c: max(nets.get(c, 0), 0) for c in CATEGORIES}
    losses = {c: max(-nets.get(c, 0), 0) for c in CATEGORIES}
    used = {c: 0 for c in CATEGORIES}
    for row in CATEGORIES:
        table[_row_key(row)]["CurrYearIncome"] = gains[row]
    for loss_column in ("AppRate", "20Per", "12_5Per"):
        targets = ["12_5Per"] if loss_column == "12_5Per" else [
            c for c in SET_OFF_ORDER if c != loss_column]
        for target in targets:
            take = min(losses[loss_column], gains[target])
            if take <= 0:
                continue
            table[_row_key(target)][LOSS_FIELD[loss_column]] = take
            gains[target] -= take
            losses[loss_column] -= take
            used[loss_column] += take
    for row in CATEGORIES:
        table[_row_key(row)]["CurrYrCapGain"] = gains[row]
        table["InLossSetOff"][LOSS_FIELD[row]] = max(-nets.get(row, 0), 0)
        table["TotLossSetOff"][LOSS_FIELD[row]] = used[row]
        table["LossRemainSetOff"][LOSS_FIELD[row]] = losses[row]
    return table


def _accruals(base: dict[str, Any], lines: list[CapitalGainLine],
              remaining: dict[str, int]) -> dict[str, Any]:
    """Split each column's gain left after current-year set-off across the advance-tax periods,
    in proportion to that column's net gain in each period (Q-025)."""
    accruals = {key: {"DateRange": dict(value["DateRange"])} for key, value in base.items()}
    for column, key in ACCRUAL_FIELD.items():
        per_period: dict[str, int] = defaultdict(int)
        for line in lines:
            if _category(line.bucket) == column:
                per_period[_period(line.disposal.sold_on)] += _rupees(line.gain)
        positive = {p: v for p, v in per_period.items() if v > 0}
        total, target = sum(positive.values()), remaining.get(column, 0)
        allocated = 0
        for index, period in enumerate(p for p in PERIODS if p in positive):
            share = (target - allocated if index == len(positive) - 1
                     else positive[period] * target // total)
            accruals[key]["DateRange"][period] = share
            allocated += share
    return accruals


def schedule_cg(report: TaxYearReport, form: str, balance_112a: int) -> dict[str, Any]:
    year = report.pack.start_year
    lines = [line for line in report.capital_gains if not line.manual]
    equity_short = [ln for ln in lines if ln.bucket.term is Term.SHORT
                    and ln.bucket.regime is Regime.EQUITY]
    slab_short = [ln for ln in lines if _category(ln.bucket) == "AppRate"]
    other_long = [ln for ln in lines if ln.bucket.term is Term.LONG
                  and not ln.bucket.exemption_eligible]
    for line in lines:
        _category(line.bucket)  # raise early for buckets the form can't hold

    schedule = skeleton(form, year, "ScheduleCGFor23")
    short = schedule["ShortTermCapGainFor23"]
    if equity_short:
        block = _sale_block(skeleton(form, year, "EquityOrUnitSec94TypeMFonSTT"), equity_short,
                            other_assets=False)
        short["EquityMFonSTT"] = [{"MFSectionCode": "1A", "EquityMFonSTTDtls": block}]
    short["SaleOnOtherAssets"] = _sale_block(short["SaleOnOtherAssets"], slab_short,
                                             other_assets=True)
    equity_stcg = short["EquityMFonSTT"][0]["EquityMFonSTTDtls"]["CapgainonAssets"] \
        if equity_short else 0
    short["TotalSTCG"] = equity_stcg + short["SaleOnOtherAssets"]["CapgainonAssets"]

    long = schedule["LongTermCapGain23"]
    long["SaleOfEquityShareUs112A"]["BalanceCG"] = balance_112a
    long["SaleOfEquityShareUs112A"]["CapgainonAssets"] = balance_112a
    other_ltcg = 0
    if other_long:
        block = _sale_block(skeleton(form, year, "EquityOrUnitSec54Type"), other_long,
                            other_assets=True)
        long["SaleofAssetNADtls"] = {"SaleofAssetNA": block}
        other_ltcg = block["CapgainonAssets"]
    long["TotalLTCG"] = balance_112a + other_ltcg

    # C and E are carried forward only when positive (schema minimum 0); losses travel through
    # the set-off table instead.
    schedule["SumOfCGIncm"] = max(short["TotalSTCG"] + long["TotalLTCG"], 0)
    schedule["TotScheduleCGFor23"] = max(schedule["SumOfCGIncm"] + schedule["IncmFromVDATrnsf"],
                                         0)

    nets = {"20Per": equity_stcg, "AppRate": short["SaleOnOtherAssets"]["CapgainonAssets"],
            "12_5Per": long["TotalLTCG"]}
    schedule["CurrYrLosses"] = _set_off_table(schedule["CurrYrLosses"], nets)
    remaining = {c: schedule["CurrYrLosses"][_row_key(c)]["CurrYrCapGain"] for c in CATEGORIES}
    schedule["AccruOrRecOfCG"] = _accruals(schedule["AccruOrRecOfCG"], lines, remaining)
    return schedule


def export_itr(report: TaxYearReport, *, form: str | None = None,
               names: Mapping[str, str] | None = None) -> ItrExport:
    """Build and validate Schedule 112A and Schedule CG for ``report``.

    ``names`` maps ISIN → share/scheme name (Schedule 112A needs a name for pre-2018 holdings).
    """
    if not report.complete:
        raise ExportError(incomplete_message(report))
    chosen = form or choose_form(report)
    if chosen == "ITR-2" and report.business.lines:
        raise ExportError("ITR-2 can't carry intraday or F&O income; use ITR-3")
    notices: list[Notice] = [Notice(
        "EXPORT", "Kosh fills only Schedule 112A and Schedule CG; the rest of the return "
        "(personal details, other income, set-off schedules, tax) must be completed in the "
        "official utility (Q-025).", question="Q-025")]
    s112a, s112a_notes = schedule_112a(report, chosen, names)
    notices += s112a_notes
    if any(line.manual for line in report.capital_gains):
        notices.append(Notice("EXPORT", "Lines Kosh couldn't compute (flagged MANUAL) are not "
                              "in the schedules; add them in the utility.", question="Q-021"))
    cg = schedule_cg(report, chosen, s112a["Balance112A"])
    schedules = {"Schedule112A": s112a, "ScheduleCGFor23": cg}
    written = json.loads(to_json(schedules), parse_float=Decimal)  # validate what is written
    errors = [e for name, value in written.items()
              for e in validate(value, chosen, report.pack.start_year, name)]
    return ItrExport(chosen, report.pack.start_year, schedules, tuple(notices), tuple(errors))
