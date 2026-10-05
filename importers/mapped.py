"""Column-mapping importer for brokers whose tradebook layout isn't documented publicly
(Groww, Angel One, others). The user says which of their column headers holds each field;
Kosh never guesses a layout (CLAUDE.md: never fabricate broker formats). See
docs/OPEN_QUESTIONS.md Q-017.
"""

from collections.abc import Iterable, Mapping

from engine.models import Segment, Side
from importers.base import ImportFormatError, ImportResult, normalise_header
from importers.tabular import (
    FIELDS,
    BrokerProfile,
    load_tradebook,
    parse_tradebook,
    parse_tradebooks,
)

MUST_MAP = ("trade_date", "side", "quantity", "price", "trade_id")
DEFAULT_SEGMENTS = {"EQ": Segment.EQUITY, "FO": Segment.FNO}
DEFAULT_SIDES = {"BUY": Side.BUY, "SELL": Side.SELL, "B": Side.BUY, "S": Side.SELL}


def mapped_profile(
    mapping: Mapping[str, str],
    *,
    source: str = "Mapped tradebook (CSV)",
    key: str = "MAPPED",
    segment_codes: Mapping[str, Segment] | None = None,
    side_codes: Mapping[str, Side] | None = None,
) -> BrokerProfile:
    """Build a profile from ``{field: header in the user's file}``.

    Fields: trade_date, side, quantity, price, trade_id (all required); isin and/or symbol
    (ISIN for cash equity, contract symbol for F&O); segment and/or exchange; optionally
    executed_at and auction. Codes are compared upper-cased.
    """
    unknown = sorted(set(mapping) - set(FIELDS))
    if unknown:
        raise ImportFormatError(f"unknown field(s) in mapping: {', '.join(unknown)}")
    missing = [f for f in MUST_MAP if not mapping.get(f)]
    if not (mapping.get("isin") or mapping.get("symbol")):
        missing.append("isin or symbol")
    if not (mapping.get("segment") or mapping.get("exchange")):
        missing.append("segment or exchange")
    if missing:
        raise ImportFormatError(f"mapping needs: {', '.join(missing)}")
    columns = {f: (normalise_header(h),) for f, h in mapping.items() if h}
    empty = [f for f, (name,) in columns.items() if not name]
    if empty:
        raise ImportFormatError(f"mapping for {', '.join(empty)} has no letters or digits")
    by_name: dict[str, list[str]] = {}
    for field, (name,) in columns.items():
        by_name.setdefault(name, []).append(field)
    shared = [fields for fields in by_name.values()
              if len(fields) > 1 and set(fields) != {"trade_date", "executed_at"}]
    if shared:  # one column may serve as both date and timestamp, nothing else
        raise ImportFormatError(f"fields {', '.join(shared[0])} are mapped to the same column")
    return BrokerProfile(
        key=key,
        source=source,
        columns=columns,
        required=tuple(columns),
        segments={k.strip().upper(): v for k, v in (segment_codes or DEFAULT_SEGMENTS).items()},
        sides={k.strip().upper(): v for k, v in (side_codes or DEFAULT_SIDES).items()},
        confirmed=False,
        notes=(f"{source}: imported with a user-supplied column mapping; check the trades "
               "against the broker's statement (docs/OPEN_QUESTIONS.md Q-017).",),
        question="Q-017",
        infer_segment=not mapping.get("segment"),
    )


def parse_mapped_tradebook(text: str, profile: BrokerProfile, *, name: str = "tradebook"
                           ) -> ImportResult:
    return parse_tradebook(text, profile, name=name)


def parse_mapped_tradebooks(files: Iterable[tuple[str, str]], profile: BrokerProfile
                            ) -> ImportResult:
    return parse_tradebooks(files, profile)


def load_mapped_tradebook(data: bytes, profile: BrokerProfile, *, name: str = "tradebook",
                          password: str | None = None) -> ImportResult:
    """CSV or XLSX bytes; Groww's XLSX is protected with the PAN in capitals."""
    return load_tradebook(data, profile, name=name, password=password)
