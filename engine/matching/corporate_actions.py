"""Corporate actions that change lots without a trade: splits and bonus issues.

Both take effect at the start of ``ex_date``, before any trade on that date.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_FLOOR

from engine.models import Lot, Segment
from engine.money import ZERO

UNVERIFIED_SPLIT = True  # surfaced as a warning on every split applied
"""Split treatment (cost unchanged, holding period carried over) follows standard practice
but no statutory citation has been confirmed. See docs/OPEN_QUESTIONS.md Q-002."""


@dataclass(frozen=True, slots=True)
class Split:
    """Sub-division: every ``old`` shares become ``new`` shares (e.g. face value 10 → 2 is 1:5).

    Total cost and acquisition date of each lot are unchanged; only quantity changes.
    UNVERIFIED (Q-002).
    """

    instrument: str
    ex_date: date
    old: int
    new: int

    def __post_init__(self) -> None:
        if self.old <= 0 or self.new <= 0:
            raise ValueError("split ratio must be positive")


@dataclass(frozen=True, slots=True)
class Bonus:
    """Bonus issue of ``bonus`` shares for every ``held`` shares.

    Cost of bonus shares is nil: Income-tax Act 1961 s.55(2)(aa)(iiia) (financial asset
    allotted without payment on the basis of holding another financial asset). Source:
    https://www.incometaxindia.gov.in/w/section-55-59 (not fetchable from dev environment).
    Income-tax Act 2025 equivalent: pending. See docs/OPEN_QUESTIONS.md Q-001.

    The bonus shares form a new lot whose holding period starts on allotment (the general
    rule in s.2(42A); no Explanation carries over the original shares' holding period).
    ``allotment_date`` defaults to ``ex_date`` when the actual date is not known — see Q-003.
    Entitlement is computed on the total holding; fractional entitlements are not allotted
    as shares and are dropped with a warning.
    """

    instrument: str
    ex_date: date
    held: int
    bonus: int
    allotment_date: date | None = None

    def __post_init__(self) -> None:
        if self.held <= 0 or self.bonus <= 0:
            raise ValueError("bonus ratio must be positive")


CorporateAction = Split | Bonus


def _adjustable(lot: Lot) -> bool:
    """Corporate actions adjust cash-equity holdings only. Exchanges adjust F&O contracts
    (strike and lot size) themselves, and intraday positions are flat at day end."""
    return lot.segment is Segment.EQUITY and lot.is_long and not lot.intraday


def apply_split(lots: list[Lot], action: Split) -> tuple[list[Lot], list[str]]:
    """Return the lots after a split, plus warnings (always the UNVERIFIED notice)."""
    warnings = [
        f"{action.instrument} split {action.old}:{action.new} on {action.ex_date}: "
        "treatment UNVERIFIED (cost and holding period carried over), see "
        "docs/OPEN_QUESTIONS.md Q-002"
    ]
    out: list[Lot] = []
    before = after = ZERO
    for lot in lots:
        if not _adjustable(lot):
            out.append(lot)
            continue
        quantity = lot.quantity * action.new / action.old
        before += lot.quantity
        after += quantity
        out.append(replace(lot, quantity=quantity))
    if (before * action.new) % action.old:
        warnings.append(
            f"{action.instrument} split {action.old}:{action.new} on {action.ex_date}: "
            f"holding of {before} becomes fractional ({after}); fractional lots kept, check "
            "the cash paid for the fraction (a sale, see Q-002)"
        )
    return out, warnings


def bonus_lot(lots: list[Lot], action: Bonus) -> tuple[Lot | None, list[str]]:
    """Return the new bonus lot for the given holdings (or None), plus warnings."""
    held = sum((lot.quantity for lot in lots if _adjustable(lot)), ZERO)
    entitled = held * action.bonus / action.held
    allotted = entitled.to_integral_value(rounding=ROUND_FLOOR)
    warnings: list[str] = []
    if entitled != allotted:
        warnings.append(
            f"{action.instrument} bonus {action.bonus}:{action.held} on {action.ex_date}: "
            f"fractional entitlement {entitled - allotted} not allotted"
        )
    if allotted == 0:
        return None, warnings
    acquired = action.allotment_date or action.ex_date
    lot = Lot(
        instrument=action.instrument,
        acquired_on=acquired,
        quantity=allotted,
        value=ZERO,
        charges=ZERO,
        stt=ZERO,
        source_trade_id=f"BONUS:{action.instrument}:{action.ex_date.isoformat()}",
    )
    return lot, warnings
