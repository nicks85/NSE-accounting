"""Public engine API. The UI and importers depend only on this module.

``compute_tax_year`` runs the whole pipeline for one tax year:
classify → FIFO (delivery with corporate actions, intraday, F&O) → capital-gain lines →
business income → set-off / exemption / carry-forward → special-rate tax.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from engine import __version__
from engine.classify.business_income import BusinessIncome, business_income
from engine.classify.capital_gains import Bucket, CapitalGainLine, Term, capital_gain_line
from engine.classify.funds import FundClass, is_fund, isin_of
from engine.classify.trades import classify_trades
from engine.dates import tax_year_bounds, tax_year_of
from engine.matching.corporate_actions import CorporateAction
from engine.matching.fifo import match_fifo
from engine.models import Disposal, Lot, Segment, Trade
from engine.money import ZERO
from engine.notices import Notice
from engine.rules import common, pack_for_year
from engine.rules.base import Citation, RulePack
from engine.rules.rounding import round_to_ten
from engine.rules.setoff import LossEntry, SetOffResult, set_off

__all__ = [
    "FundClass",
    "LossEntry",
    "Notice",
    "TaxYearReport",
    "compute_tax_year",
    "compute_tax_years",
    "engine_version",
    "unclassified_funds",
]

SCOPE_NOTE = Notice(
    "SCOPE",
    "Special-rate tax on capital gains only: surcharge, cess, rebate, slab tax on business "
    "income, the basic-exemption shortfall adjustment (s.196(2)/s.198(3)) and rounding of "
    "total income to ₹10 before tax are not applied.",
    question="Q-012",
)


def engine_version() -> str:
    """Return the engine version string."""
    return __version__


@dataclass(frozen=True, slots=True)
class TaxYearReport:
    pack: RulePack
    capital_gains: tuple[CapitalGainLine, ...]
    bucket_nets: Mapping[Bucket, Decimal]
    business: BusinessIncome
    setoff: SetOffResult
    special_rate_tax: Decimal
    special_rate_tax_rounded: Decimal
    open_lots: tuple[Lot, ...]
    warnings: tuple[Notice, ...]
    unverified: tuple[Citation, ...] = field(default=())

    @property
    def carried_forward(self) -> tuple[LossEntry, ...]:
        return self.setoff.carried_forward


def _in_year(disposals: Iterable[Disposal], start_year: int) -> list[Disposal]:
    return [d for d in disposals if tax_year_of(d.sold_on) == start_year]


def compute_tax_year(
    start_year: int,
    trades: Iterable[Trade],
    *,
    opening_lots: Iterable[Lot] = (),
    actions: Iterable[CorporateAction] = (),
    fmv_2018: Mapping[str, Decimal] | None = None,
    fund_classes: Mapping[str, FundClass] | None = None,
    brought_forward: Iterable[LossEntry] = (),
) -> TaxYearReport:
    """Compute one tax year (``start_year`` 2024 → FY 2024-25).

    ``trades`` may span several years; history is needed for FIFO. Only disposals closed in
    the requested year are taxed. ``fmv_2018`` maps ISIN → 31-Jan-2018 FMV per share or unit
    (NAV for fund units). ``fund_classes`` maps a fund's ISIN → its ``FundClass``.
    """
    pack = pack_for_year(start_year)
    _, year_end = tax_year_bounds(start_year)
    history = [t for t in trades if t.trade_date <= year_end]
    action_list = [a for a in actions if a.ex_date <= year_end]

    classified = classify_trades(history)
    delivery = match_fifo(classified.delivery, opening_lots, action_list)
    intraday = match_fifo(classified.intraday, allow_short=True)
    fno = match_fifo(classified.fno)
    warnings = [
        Notice("ENGINE", message)
        for message in (*classified.warnings, *delivery.warnings, *intraday.warnings,
                        *fno.warnings)
    ]

    lines: list[CapitalGainLine] = []
    for disposal in _in_year(delivery.disposals, start_year):
        line, line_notices = capital_gain_line(disposal, pack, fmv_2018 or {}, fund_classes)
        lines.append(line)
        warnings.extend(line_notices)
        if disposal.stripped_loss:
            warnings.append(Notice(
                "BONUS_STRIPPING",
                f"Loss of ₹{disposal.stripped_loss} on {disposal.instrument} bought "
                f"{disposal.acquired_on} is ignored (bonus stripping, 2025 Act s.175(9),(10); "
                "1961 Act s.94(8)) and added to the cost of the bonus shares still held",
                ref=disposal.close_trade_id,
            ))

    nets: dict[Bucket, Decimal] = {}
    gains: dict[Bucket, Decimal] = {}
    losses: dict[Term, Decimal] = {}
    for line in lines:
        if line.manual:
            continue
        nets[line.bucket] = nets.get(line.bucket, ZERO) + line.gain
        if line.gain > 0:
            gains[line.bucket] = gains.get(line.bucket, ZERO) + line.gain
        elif line.gain < 0:
            losses[line.bucket.term] = losses.get(line.bucket.term, ZERO) - line.gain
    business = business_income(_in_year(intraday.disposals, start_year),
                               _in_year(fno.disposals, start_year))
    result = set_off(pack, gains, losses, business.speculative, business.non_speculative,
                     brought_forward)
    tax = result.tax()

    used: list[Citation] = [c for line in lines for c in line.citations]
    used += [c for line in business.lines for c in line.citations]
    used += [step.citation for step in result.steps]
    long_term_rates = {b.rate for b in nets if b.exemption_eligible}
    if result.exemption_used and len(long_term_rates) > 1:  # FY 2024-25 split rates
        used += [c for c in pack.citations.values() if c.question == "Q-007"]
    if result.order_assumed:
        used.append(common.SETOFF_ORDER)
    if result.carried_forward:
        used.append(common.RETURN_OF_LOSS)
    unverified = tuple(dict.fromkeys(c for c in used if c.unverified))
    warnings += [
        Notice("UNVERIFIED", f"{c.topic} (best guess; see docs/OPEN_QUESTIONS.md "
               f"{c.question}).", question=c.question)
        for c in unverified
    ]
    warnings += [
        Notice("EXPIRED_LOSS", f"{e.kind.value} of {e.origin_year} (₹{e.amount}) is past its "
               "carry-forward period and was not used")
        for e in result.expired
    ]
    warnings.append(SCOPE_NOTE)

    return TaxYearReport(
        pack=pack,
        capital_gains=tuple(lines),
        bucket_nets=MappingProxyType(nets),
        business=business,
        setoff=result,
        special_rate_tax=tax,
        special_rate_tax_rounded=round_to_ten(tax),
        open_lots=delivery.open_lots,
        warnings=tuple(warnings),
        unverified=unverified,
    )


def compute_tax_years(
    start_years: Iterable[int],
    trades: Iterable[Trade],
    *,
    opening_lots: Iterable[Lot] = (),
    actions: Iterable[CorporateAction] = (),
    fmv_2018: Mapping[str, Decimal] | None = None,
    fund_classes: Mapping[str, FundClass] | None = None,
    fund_classes_by_year: Mapping[int, Mapping[str, FundClass]] | None = None,
    brought_forward: Iterable[LossEntry] = (),
) -> list[TaxYearReport]:
    """Compute consecutive years, carrying each year's unabsorbed losses into the next.

    A fund's class can change between years (the meaning of "specified fund" changed in
    FY 2025-26): ``fund_classes_by_year[year]`` overrides ``fund_classes`` for that year."""
    trade_list, lot_list, action_list = list(trades), list(opening_lots), list(actions)
    years = sorted(start_years)
    if years and years != list(range(years[0], years[-1] + 1)):
        raise ValueError(f"tax years must be consecutive to carry losses forward: {years}")
    carried = list(brought_forward)
    reports = []
    for year in years:
        report = compute_tax_year(year, trade_list, opening_lots=lot_list, actions=action_list,
                                  fmv_2018=fmv_2018,
                                  fund_classes={**(fund_classes or {}),
                                                **(fund_classes_by_year or {}).get(year, {})},
                                  brought_forward=carried)
        reports.append(report)
        carried = list(report.carried_forward)
    return reports


def unclassified_funds(trades: Iterable[Trade], fund_classes: Mapping[str, FundClass]
                       ) -> list[str]:
    """ISINs of fund units (statement units or exchange-traded INF… ISINs) in ``trades`` that
    have no class yet, for the UI's classification step."""
    isins = {isin_of(t.instrument) for t in trades
             if t.segment is Segment.MUTUAL_FUND or is_fund(t.instrument)}
    return sorted(isins - set(fund_classes))
