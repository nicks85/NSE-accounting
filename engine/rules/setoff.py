"""Set-off of losses within a tax year, brought-forward losses, the s.112A/s.198 exemption,
and the carry-forward ledger.

Order of operations (each step cites its rule in ``engine/rules/common.py``):
1. Current-year capital losses: long-term loss against LTCG only; short-term loss against any
   capital gain — 2025 Act s.108(2); 1961 Act s.70(2),(3).
2. F&O (non-speculative) loss against speculative income in the same head — 2025 Act s.108(1);
   1961 Act s.70(1). Speculative loss is never set off against other income — s.113 / s.73.
3. Remaining F&O loss against capital gains — 2025 Act s.109; 1961 Act s.71. UNVERIFIED
   (Q-010): the taxpayer's other heads of income are not known to Kosh.
4. Brought-forward losses, oldest first, if within their carry-forward period: business loss
   against business income (s.112 / s.72, 8 years); speculative loss against speculative income
   (s.113 / s.73, 4 years); long-term capital loss against LTCG, then short-term capital loss
   against any capital gain (s.111 / s.74, 8 years).
5. LTCG exemption (₹1.25 lakh) on what remains — s.198(2)(a) / s.112A.
6. Unabsorbed losses are carried forward (subject to filing the return on time, Q-011).

Within each step, losses are set off against the highest-rate gains first, treating slab-rate
gains as the highest and, at equal rates, gains outside the ₹1.25 lakh exemption before
exemption-eligible LTCG. The Act leaves the choice to the taxpayer; this order minimises tax
in most cases but not all (e.g. a low slab rate). UNVERIFIED (Q-008).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from engine.classify.capital_gains import Bucket, Term
from engine.money import ZERO, require_decimal
from engine.rules import common
from engine.rules.base import Citation, RulePack


class LossKind(StrEnum):
    SHORT_TERM_CAPITAL = "Short-term capital loss"
    LONG_TERM_CAPITAL = "Long-term capital loss"
    SPECULATIVE = "Speculative business loss"
    BUSINESS = "Non-speculative business loss"


@dataclass(frozen=True, slots=True)
class LossEntry:
    origin_year: int
    """Start year of the tax year in which the loss arose."""
    kind: LossKind
    amount: Decimal

    def __post_init__(self) -> None:
        require_decimal("amount", self.amount)
        if self.amount <= 0:
            raise ValueError(f"loss amount must be positive, got {self.amount}")


@dataclass(frozen=True, slots=True)
class SetOffStep:
    loss: str
    against: str
    amount: Decimal
    citation: Citation


@dataclass(frozen=True, slots=True)
class SetOffResult:
    gains: dict[Bucket, Decimal]
    """Taxable capital gains per bucket after set-off and exemption."""
    speculative_income: Decimal
    business_income: Decimal
    exemption_used: dict[Bucket, Decimal]
    steps: tuple[SetOffStep, ...]
    carried_forward: tuple[LossEntry, ...]
    expired: tuple[LossEntry, ...]
    order_assumed: bool = False
    """True when a loss could go to more than one bucket and the Q-008 order decided it."""

    def tax(self) -> Decimal:
        """Tax at the special rates; slab-rate buckets are reported but not taxed here."""
        return sum((amount * bucket.rate for bucket, amount in self.gains.items()
                    if bucket.rate is not None), ZERO)


SPECULATIVE_LABEL = "Speculative income"
BUSINESS_LABEL = "Non-speculative business income"


def carry_years(pack: RulePack, kind: LossKind) -> int:
    if kind is LossKind.SPECULATIVE:
        return pack.speculative_loss_carry_years
    if kind is LossKind.BUSINESS:
        return pack.business_loss_carry_years
    return pack.capital_loss_carry_years


class _Pools:
    def __init__(self, gains: dict[Bucket, Decimal], speculative: Decimal, business: Decimal):
        self.gains = gains
        self.income = {SPECULATIVE_LABEL: speculative, BUSINESS_LABEL: business}
        self.steps: list[SetOffStep] = []
        self.order_assumed = False

    def by_rate(self, term: Term | None) -> list[Bucket]:
        buckets = [b for b in self.gains if term is None or b.term is term]
        return sorted(buckets, key=lambda b: b.sort_key)

    def against_gains(
        self, amount: Decimal, targets: list[Bucket], loss: str, citation: Citation
    ) -> Decimal:
        if amount > 0 and sum(1 for b in targets if self.gains[b] > 0) > 1:
            self.order_assumed = True  # a choice between eligible buckets was made (Q-008)
        for bucket in targets:
            take = min(amount, self.gains[bucket])
            if take > 0:
                self.gains[bucket] -= take
                amount -= take
                self.steps.append(SetOffStep(loss, bucket.label, take, citation))
        return amount

    def against_income(
        self, amount: Decimal, labels: list[str], loss: str, citation: Citation
    ) -> Decimal:
        for label in labels:
            take = min(amount, max(self.income[label], ZERO))
            if take > 0:
                self.income[label] -= take
                amount -= take
                self.steps.append(SetOffStep(loss, label, take, citation))
        return amount


def set_off(
    pack: RulePack,
    gains: Mapping[Bucket, Decimal],
    losses: Mapping[Term, Decimal],
    speculative: Decimal,
    business: Decimal,
    brought_forward: Iterable[LossEntry] = (),
) -> SetOffResult:
    """``gains`` are the positive gains per bucket and ``losses`` the capital losses per term
    (as positive amounts), kept apart so a loss isn't absorbed by gains in its own bucket before
    the set-off order is applied (e.g. against exemption-eligible LTCG)."""
    year = pack.start_year
    pools = _Pools({b: g for b, g in gains.items() if g > 0}, speculative, business)
    any_gain = pools.by_rate(None)
    st_then_lt = pools.by_rate(Term.SHORT) + pools.by_rate(Term.LONG)
    long_only = pools.by_rate(Term.LONG)
    stcl = losses.get(Term.SHORT, ZERO)
    ltcl = losses.get(Term.LONG, ZERO)

    # 1. Current-year capital losses.
    ltcl = pools.against_gains(ltcl, long_only, "Current-year LTCL", common.SETOFF_CAPITAL)
    stcl = pools.against_gains(stcl, st_then_lt, "Current-year STCL", common.SETOFF_CAPITAL)

    # 2-3. Current-year F&O loss: same head first, then capital gains.
    if pools.income[BUSINESS_LABEL] < 0:
        loss = -pools.income[BUSINESS_LABEL]
        pools.income[BUSINESS_LABEL] = ZERO
        loss = pools.against_income(loss, [SPECULATIVE_LABEL], "Current-year F&O loss",
                                    common.SETOFF_SAME_HEAD)
        loss = pools.against_gains(loss, any_gain, "Current-year F&O loss",
                                   common.INTER_HEAD_AGAINST_CG)
        pools.income[BUSINESS_LABEL] = -loss

    # 4. Brought-forward losses.
    carried: list[LossEntry] = []
    expired: list[LossEntry] = []
    # Speculative before business: a b/f business loss may also absorb speculative income,
    # so it must not use up income that only a (shorter-lived) speculative loss can use.
    kinds = (LossKind.SPECULATIVE, LossKind.BUSINESS, LossKind.LONG_TERM_CAPITAL,
             LossKind.SHORT_TERM_CAPITAL)
    entries = sorted(brought_forward, key=lambda e: (kinds.index(e.kind), e.origin_year))
    for entry in entries:
        if entry.origin_year >= year:
            raise ValueError(f"brought-forward loss from {entry.origin_year} is not before {year}")
        if year - entry.origin_year > carry_years(pack, entry.kind):
            expired.append(entry)
            continue
        label = f"Brought-forward {entry.kind.value.lower()} of {entry.origin_year}"
        if entry.kind is LossKind.BUSINESS:
            left = pools.against_income(entry.amount, [BUSINESS_LABEL, SPECULATIVE_LABEL],
                                        label, common.CARRY_BUSINESS)
        elif entry.kind is LossKind.SPECULATIVE:
            left = pools.against_income(entry.amount, [SPECULATIVE_LABEL], label,
                                        common.CARRY_SPECULATIVE)
        elif entry.kind is LossKind.LONG_TERM_CAPITAL:
            left = pools.against_gains(entry.amount, long_only, label, common.CARRY_CAPITAL)
        else:
            left = pools.against_gains(entry.amount, st_then_lt, label, common.CARRY_CAPITAL)
        if left > 0:
            carried.append(LossEntry(entry.origin_year, entry.kind, left))

    # 5. LTCG exemption.
    exemption_left = pack.ltcg_exemption
    exemption_used: dict[Bucket, Decimal] = {}
    for bucket in (b for b in long_only if b.exemption_eligible):
        take = min(exemption_left, pools.gains[bucket])
        if take > 0:
            pools.gains[bucket] -= take
            exemption_left -= take
            exemption_used[bucket] = take
            pools.steps.append(SetOffStep("LTCG exemption", bucket.label, take,
                                          common.LTCG_EXEMPTION))

    # 6. Current-year losses to carry forward.
    for kind, amount in (
        (LossKind.SHORT_TERM_CAPITAL, stcl),
        (LossKind.LONG_TERM_CAPITAL, ltcl),
        (LossKind.SPECULATIVE, -min(pools.income[SPECULATIVE_LABEL], ZERO)),
        (LossKind.BUSINESS, -min(pools.income[BUSINESS_LABEL], ZERO)),
    ):
        if amount > 0:
            carried.append(LossEntry(year, kind, amount))

    return SetOffResult(
        gains=pools.gains,
        speculative_income=max(pools.income[SPECULATIVE_LABEL], ZERO),
        business_income=max(pools.income[BUSINESS_LABEL], ZERO),
        exemption_used=exemption_used,
        steps=tuple(pools.steps),
        carried_forward=tuple(sorted(carried, key=lambda e: (e.origin_year, kinds.index(e.kind)))),
        expired=tuple(expired),
        order_assumed=pools.order_assumed,
    )
