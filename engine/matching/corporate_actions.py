"""Corporate actions that change lots without a trade: splits and bonus issues.

Both take effect at the start of ``ex_date``, before any trade on that date.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_FLOOR

from engine.models import Lot, Segment
from engine.money import ZERO

ACT_2025 = "https://www.incometaxindia.gov.in/documents/d/guest/income_tax_act_2025_as_amended_by_fa_act_2026-pdf"

GRANDFATHERING_FMV_DATE = date(2018, 1, 31)
"""FMV date for grandfathering: 2025 Act s.90(8)(b); 1961 Act s.55(2)(ac). Splits after this
date change the number of shares per share whose FMV was published."""

UNVERIFIED_SPLIT = True  # surfaced as a warning on every split applied
"""The cost side of a split is cited (2025 Act s.90(9)(d); 1961 Act s.55(2)(b)(v)), but carrying
over the holding period rests on practice, not an explicit provision. See Q-002."""


@dataclass(frozen=True, slots=True)
class Split:
    """Sub-division or consolidation: every ``old`` shares become ``new`` shares
    (e.g. face value 10 → 2 is 1:5).

    Cost follows the original shares: Income-tax Act 2025 s.90(9)(d)(i),(iv); 1961 Act
    s.55(2)(b)(v). Source: ``ACT_2025``. The acquisition date is carried over too — that
    part is UNVERIFIED (docs/OPEN_QUESTIONS.md Q-002).
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

    Cost of bonus shares is nil: Income-tax Act 2025 s.90(5)(b), s.90(6)(d); 1961 Act
    s.55(2)(aa)(iiia). Source: ``ACT_2025``.

    The bonus shares form a new lot whose holding period runs from the date of allotment:
    2025 Act s.2(101)(c)(C)(IV); 1961 Act s.2(42A). ``allotment_date`` defaults to
    ``ex_date`` when the actual date is not known — see docs/OPEN_QUESTIONS.md Q-003.
    Bonus stripping (2025 Act s.175(9),(10); 1961 Act s.94(8)) is applied by the FIFO book,
    using ``record_date`` (defaults to ``ex_date``: equal under T+1 settlement).
    Entitlement is computed on the total holding; fractional entitlements are not allotted
    as shares and are dropped with a warning.
    """

    instrument: str
    ex_date: date
    held: int
    bonus: int
    allotment_date: date | None = None
    record_date: date | None = None

    @property
    def record_on(self) -> date:
        return self.record_date or self.ex_date

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
        factor = lot.split_factor
        if action.ex_date > GRANDFATHERING_FMV_DATE:
            factor = factor * action.new / action.old
        out.append(replace(lot, quantity=quantity, split_factor=factor))
    if (before * action.new) % action.old:
        warnings.append(
            f"{action.instrument} split {action.old}:{action.new} on {action.ex_date}: "
            f"holding of {before} becomes fractional ({after}); fractional lots kept, check "
            "the cash paid for the fraction (a sale, see Q-002)"
        )
    return out, warnings


BONUS_PREFIX = "BONUS:"


def bonus_lot_id(action: Bonus) -> str:
    return f"{BONUS_PREFIX}{action.instrument}:{action.ex_date.isoformat()}"


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
        source_trade_id=bonus_lot_id(action),
    )
    return lot, warnings
