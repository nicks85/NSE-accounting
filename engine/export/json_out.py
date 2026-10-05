"""Serialise export structures to JSON text with ``Decimal`` written exactly (never via float,
CLAUDE.md rule 2)."""

import json
from decimal import Decimal
from typing import Any


def to_json(value: Any, *, indent: int = 2) -> str:
    return _encode(value, 0, indent)


def _encode(value: Any, level: int, indent: int) -> str:
    pad, inner = " " * indent * level, " " * indent * (level + 1)
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"cannot write non-finite amount {value}")
        return format(value, "f")
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = ",\n".join(f"{inner}{json.dumps(str(k), ensure_ascii=False)}: "
                           f"{_encode(v, level + 1, indent)}" for k, v in value.items())
        return "{\n" + items + "\n" + pad + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = ",\n".join(inner + _encode(v, level + 1, indent) for v in value)
        return "[\n" + items + "\n" + pad + "]"
    raise TypeError(f"cannot serialise {type(value).__name__}")
