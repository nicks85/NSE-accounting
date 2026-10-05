"""PDF summary of a tax-year report: totals, set-off, line-by-line gains with the rule behind
each line, business income, carried-forward losses and every warning / UNVERIFIED item.

Rendered with reportlab from local data only. reportlab can fetch images from URLs; that is off
by default (``trustedHosts = None``) and is pinned off here as well, and Kosh never passes a
URL. Output is reproducible (``invariant``): the same report gives the same bytes. Amounts are
shown in Indian digit grouping, rounded to paise for display only.
"""

import io
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, PropertySet, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from engine import __version__
from engine.api import TaxYearReport
from engine.classify.funds import isin_of

rl_config.trustedHosts = None  # never fetch anything from the network

DISCLAIMER = (
    "Kosh is a calculation aid, not tax, legal or financial advice. Verify every figure against "
    "your AIS/TIS and broker statements, and with a qualified Chartered Accountant, before "
    "filing. Items marked UNVERIFIED are best guesses listed in docs/OPEN_QUESTIONS.md."
)
GRID = TableStyle([
    ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9aa7b1")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
])


def inr(amount: Decimal) -> str:
    """Rs. with Indian grouping: 1234567.891 → 'Rs. 12,34,567.89'; -5 → '-Rs. 5.00'."""
    value = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign, digits = ("-", -value) if value < 0 else ("", value)
    whole, _, fraction = f"{digits:.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join([*groups, tail]) if groups else tail
    return f"{sign}Rs. {text}.{fraction}"


def _table(rows: Sequence[list[Any]], widths: list[float], left_columns: int = 1) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    style = TableStyle(GRID.getCommands())
    for column in range(left_columns):
        style.add("ALIGN", (column, 0), (column, -1), "LEFT")
    table.setStyle(style)
    return table


def render_summary(report: TaxYearReport, *, names: Mapping[str, str] | None = None) -> bytes:
    """Render ``report`` as a PDF and return its bytes."""
    names = names or {}
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8, leading=10)
    small = ParagraphStyle("small", parent=body, fontSize=7, leading=9)
    act = report.pack.act
    story: list[object] = [
        _p(f"Kosh — tax summary for {report.pack.label}", styles["Title"]),
        _p(f"Governing law: {act.value}. Engine {__version__}.", body),
        _p(DISCLAIMER, small),
        Spacer(1, 4 * mm),
        _p("Summary", styles["Heading2"]),
    ]

    rows: list[list[Any]]
    summary = [["Item", "Amount"]]
    for bucket, net in sorted(report.bucket_nets.items(), key=lambda kv: kv[0].sort_key):
        summary.append([_escape(f"Net {bucket.label}"), inr(net)])
    for bucket, used in report.setoff.exemption_used.items():
        summary.append([f"LTCG exemption used ({bucket.label})", inr(used)])
    for bucket, taxable in sorted(report.setoff.gains.items(), key=lambda kv: kv[0].sort_key):
        if taxable:
            summary.append([f"Taxable {bucket.label}", inr(taxable)])
    summary += [
        ["Tax at special rates (exact)", inr(report.special_rate_tax)],
        ["Tax at special rates (rounded to Rs. 10)", inr(report.special_rate_tax_rounded)],
        ["Speculative (intraday) income before set-off", inr(report.business.speculative)],
        ["Non-speculative (F&O) income before set-off", inr(report.business.non_speculative)],
        ["Speculative income after set-off", inr(report.setoff.speculative_income)],
        ["Non-speculative income after set-off", inr(report.setoff.business_income)],
    ]
    story.append(_table(summary, [120 * mm, 45 * mm]))
    story.append(_p("Slab-rate gains and business income are taxed at your slab rates, "
                           "outside this figure; surcharge and cess are not included.", small))

    if report.setoff.steps:
        story += [_p("Set-off of losses and exemption", styles["Heading2"])]
        rows = [["Loss / relief", "Set off against", "Amount", "Rule"]]
        rows += [[step.loss, step.against, inr(step.amount),
                  f"{step.citation.section(act)} — {step.citation.topic}"]
                 for step in report.setoff.steps]
        story.append(_table([[_p(c, small) if i in (0, 3) else _escape(c)
                              for i, c in enumerate(r)] for r in rows],
                            [55 * mm, 45 * mm, 28 * mm, 55 * mm], left_columns=2))

    if report.carried_forward or report.setoff.expired:
        story += [_p("Losses carried forward", styles["Heading2"])]
        rows = [["Loss", "Year of loss", "Amount", "Status"]]
        rows += [[e.kind.value, f"FY {e.origin_year}-{(e.origin_year + 1) % 100:02d}",
                  inr(e.amount), "carried forward"] for e in report.carried_forward]
        rows += [[e.kind.value, f"FY {e.origin_year}-{(e.origin_year + 1) % 100:02d}",
                  inr(e.amount), "expired, not usable"] for e in report.setoff.expired]
        story.append(_table(rows, [65 * mm, 30 * mm, 35 * mm, 40 * mm], left_columns=2))

    if report.capital_gains:
        story += [_p("Capital gains, line by line", styles["Heading2"])]
        rows = [["Instrument", "Acquired", "Sold", "Qty", "Sale value", "Cost used", "Gain",
                 "Bucket", "Rules"]]
        for line in report.capital_gains:
            d = line.disposal
            isin = isin_of(d.instrument)
            label = f"{names[isin]} ({d.instrument})" if isin in names else d.instrument
            rules = "; ".join(sorted({c.section(act) for c in line.citations}))
            rows.append([_p(label, small), d.acquired_on.isoformat(),
                         d.sold_on.isoformat(), f"{d.quantity.normalize():f}",
                         inr(d.sale_value), inr(line.cost), inr(line.gain),
                         "MANUAL — not computed" if line.manual else line.bucket.label,
                         _p(rules, small)])
        story.append(_table(rows, [48 * mm, 18 * mm, 18 * mm, 16 * mm, 27 * mm, 27 * mm,
                                   25 * mm, 34 * mm, 50 * mm], left_columns=3))

    if report.business.lines:
        story += [_p("Business income (intraday and F&O), line by line",
                            styles["Heading2"])]
        rows = [["Instrument", "Opened", "Closed", "Qty", "Income after STT", "Kind"]]
        rows += [[_p(bl.disposal.instrument, small), bl.disposal.acquired_on.isoformat(),
                  bl.disposal.sold_on.isoformat(), f"{bl.disposal.quantity.normalize():f}",
                  inr(bl.income), "Speculative" if bl.speculative else "Non-speculative"]
                 for bl in report.business.lines]
        story.append(_table(rows, [70 * mm, 22 * mm, 22 * mm, 20 * mm, 35 * mm, 30 * mm],
                            left_columns=3))

    story += [_p("Warnings and assumptions", styles["Heading2"])]
    for notice in report.warnings:
        tag = f"[{notice.code}{' ' + notice.question if notice.question else ''}]"
        story.append(Paragraph(f"<b>{_escape(tag)}</b> {_escape(notice.message)}", small))

    out = io.BytesIO()
    document = SimpleDocTemplate(
        out, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm, invariant=1,
        title=f"Kosh tax summary {report.pack.label}", author="Kosh", creator="Kosh",
    )
    document.build(story)  # type: ignore[arg-type]
    return out.getvalue()


def _escape(text: str) -> str:
    """Make text safe for reportlab's mini-markup and the built-in Helvetica font: escape
    markup characters, write ₹ as "Rs. ", and replace anything outside Windows-1252 (which the
    font can't draw) with "?"."""
    text = text.replace("₹", "Rs. ").replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;")
    return text.encode("cp1252", errors="replace").decode("cp1252")


def _p(text: str, style: PropertySet) -> Paragraph:
    return Paragraph(_escape(text), style)
