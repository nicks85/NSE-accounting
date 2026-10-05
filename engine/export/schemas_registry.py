"""Load the official CBDT ITR JSON schemas bundled in engine/export/schemas/ and validate
fragments against their definitions.

Validation uses jsonschema's Draft 4 validator with an empty ``referencing`` registry and no
retrieve function, so only the schema's own ``#/definitions`` references resolve; a remote
``$ref`` raises instead of being fetched (no network, CLAUDE.md rule 1). jsonschema's
deprecated ``RefResolver`` (which can fetch over HTTP) is never used.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft4Validator, ValidationError, validators
from referencing import Registry

SCHEMAS = {
    ("ITR-2", 2025): "ITR-2_AY2026-27.json",
    ("ITR-3", 2025): "ITR-3_AY2026-27.json",
}
"""(form, tax-year start) → bundled file. FY 2025-26 is assessment year 2026-27."""


class UnsupportedFormError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SchemaError:
    path: str
    message: str


@cache
def load_schema(form: str, start_year: int) -> dict[str, Any]:
    try:
        name = SCHEMAS[(form, start_year)]
    except KeyError:
        supported = ", ".join(f"{f} for FY {y}-{(y + 1) % 100:02d}" for f, y in SCHEMAS)
        raise UnsupportedFormError(
            f"no official schema bundled for {form}, FY {start_year}-{(start_year + 1) % 100:02d}"
            f" (bundled: {supported})"
        ) from None
    text = resources.files("engine.export.schemas").joinpath(name).read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


def _multiple_of(validator: Any, divisor: Any, instance: Any, schema: Any) -> Iterator[Any]:
    """Exact ``multipleOf`` on Decimals: the schema's float divisors (e.g. 0.0001) are compared
    via their decimal text, so 1.1 is a multiple of 0.0001 (float division says otherwise)."""
    if not validator.is_type(instance, "number"):
        return
    if Decimal(str(instance)) % Decimal(str(divisor)) != 0:
        yield ValidationError(f"{instance} is not a multiple of {divisor}")


_Validator = validators.extend(  # type: ignore[no-untyped-call]
    Draft4Validator, {"multipleOf": _multiple_of})


def iter_errors(fragment: Any, form: str, start_year: int, name: str) -> Iterator[SchemaError]:
    """Errors from validating ``fragment`` against ``#/definitions/<name>`` of the schema.
    Pass the parsed export text (numbers as Decimal) to validate exactly what is written."""
    schema = load_schema(form, start_year)
    wrapper = {"$ref": f"#/definitions/{name}", "definitions": schema["definitions"]}
    validator = _Validator(wrapper, registry=Registry())
    for error in validator.iter_errors(fragment):
        path = "/".join(str(p) for p in error.absolute_path)
        yield SchemaError(f"{name}/{path}" if path else name, error.message)


def validate(fragment: Any, form: str, start_year: int, name: str) -> list[SchemaError]:
    return sorted(iter_errors(fragment, form, start_year, name), key=lambda e: e.path)
