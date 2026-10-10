"""Filed years (brief 0001 D2, task 4): what changed since a year's return was filed.

When the user marks a year as filed, its report is kept exactly as it was (the "as filed"
snapshot). Later imports or edits can change that year's figures, for example an older
tradebook changes FIFO, and that may mean a revised return. Kosh doesn't block the change:
it lists every figure that differs, as filed and now (answer 9 in brief 0001).

Works on the RPC report JSON, the form the UI shows and the snapshot stores.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

JSON = Mapping[str, Any]


def _fy(start_year: int) -> str:
    return f"FY {start_year}-{(start_year + 1) % 100:02d}"


def figures(report: JSON) -> dict[str, Decimal]:
    """The figures a return is built from, by a readable label.

    Known limit: if a later engine version renames a bucket label, the old label shows as gone
    and the new one as added; the snapshot's engine version is shown alongside."""
    summary = report["summary"]
    out: dict[str, Decimal] = {}
    for key, prefix in (("bucket_nets", "Net"), ("exemption_used", "₹1.25 lakh exemption used,"),
                        ("taxable", "Taxable")):
        for item in summary[key]:
            out[f"{prefix} {item['label']}"] = Decimal(item["amount"])
    out["Tax at special rates (rounded)"] = Decimal(summary["special_rate_tax_rounded"])
    out["Intraday income after set-off"] = Decimal(summary["speculative_after_setoff"])
    out["F&O income after set-off"] = Decimal(summary["non_speculative_after_setoff"])
    out["Intraday income before set-off"] = Decimal(summary["speculative_income"])
    out["F&O income before set-off"] = Decimal(summary["non_speculative_income"])
    lines = report["capital_gains"]
    out["Total sale value"] = sum((Decimal(x["sale_value"]) for x in lines), Decimal(0))
    out["Total cost"] = sum((Decimal(x["cost"]) for x in lines), Decimal(0))
    out["Total transfer expenses"] = sum((Decimal(x["transfer_expenses"]) for x in lines),
                                         Decimal(0))
    for step in report["setoff_steps"]:
        if step["loss"].startswith("Brought-forward"):
            _add(out, f"Set off: {step['loss']}", Decimal(step["amount"]))
    for loss in report["carried_forward"]:
        _add(out, f"Carried forward: {loss['kind']} of {_fy(loss['origin_year'])}",
             Decimal(loss["amount"]))
    out["Number of capital-gain lines"] = Decimal(len(lines))
    out["Number of intraday and F&O lines"] = Decimal(len(report["business_lines"]))
    return out


def _add(out: dict[str, Decimal], label: str, amount: Decimal) -> None:
    """Two entries with the same label (two losses of one kind and year) are added up, so a
    change to either one shows."""
    out[label] = out.get(label, Decimal(0)) + amount


def changes(filed: JSON, now: JSON) -> list[dict[str, str | None]]:
    """Every figure that differs: ``{"item", "filed", "now"}``, with None where a figure
    exists on one side only. Amounts are compared by value (``10`` equals ``10.00``)."""
    before, after = figures(filed), figures(now)
    out: list[dict[str, str | None]] = []
    for label in [*before, *(k for k in after if k not in before)]:
        old, new = before.get(label), after.get(label)
        if (old is None and new == 0) or (new is None and old == 0):
            continue  # a zero line appearing or going isn't a change in the return
        if old != new:
            out.append({"item": label, "filed": None if old is None else str(old),
                        "now": None if new is None else str(new)})
    return out
