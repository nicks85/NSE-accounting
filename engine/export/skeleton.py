"""Build the minimal object the official schema requires for a definition: every required
integer/number is 0, every required object is built recursively, every required array is
empty. Kosh then overlays its own figures. Deriving the skeleton from the schema keeps the
structure in step with the published form instead of hand-copying it."""

from typing import Any

from engine.export.schemas_registry import load_schema


class SkeletonError(ValueError):
    """A required field has no neutral default (e.g. a required enum or free-text string)."""


def _resolve(node: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    while True:
        ref = node.get("$ref") or next(
            (part["$ref"] for part in node.get("allOf", []) if "$ref" in part
             and part["$ref"] != "#/definitions/nonEmptyString"), None)
        if ref is None:
            return node
        node = {**definitions[ref.rsplit("/", 1)[-1]], **{k: v for k, v in node.items()
                                                          if k not in ("$ref", "allOf")}}


def _kind(node: dict[str, Any]) -> str:
    if "type" in node:
        return str(node["type"])
    if "properties" in node:
        return "object"
    if "items" in node:
        return "array"
    return "string"


def skeleton(form: str, start_year: int, name: str) -> dict[str, Any]:
    definitions = load_schema(form, start_year)["definitions"]
    value = _build(definitions[name], definitions, name)
    assert isinstance(value, dict)  # noqa: S101 - every schedule is an object
    return value


def _build(node: dict[str, Any], definitions: dict[str, Any], where: str) -> Any:
    node = _resolve(node, definitions)
    kind = _kind(node)
    if kind == "object":
        properties = node.get("properties", {})
        return {key: _build(properties[key], definitions, f"{where}/{key}")
                for key in node.get("required", [])}
    if kind == "array":
        if node.get("minItems", 0):
            raise SkeletonError(f"{where}: required non-empty array")
        return []
    if kind in ("integer", "number"):
        return 0
    raise SkeletonError(f"{where}: required {kind} has no neutral default")
