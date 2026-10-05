"""Versioned tax rule packs, one module per tax year."""

from engine.rules import fy2024_25, fy2025_26, ty2026_27
from engine.rules.base import RulePack

PACKS: dict[int, RulePack] = {
    p.start_year: p for p in (fy2024_25.PACK, fy2025_26.PACK, ty2026_27.PACK)
}


class UnsupportedTaxYearError(ValueError):
    pass


def pack_for_year(start_year: int) -> RulePack:
    try:
        return PACKS[start_year]
    except KeyError:
        supported = ", ".join(p.label for p in PACKS.values())
        raise UnsupportedTaxYearError(
            f"tax year starting {start_year} is not supported (supported: {supported})"
        ) from None
