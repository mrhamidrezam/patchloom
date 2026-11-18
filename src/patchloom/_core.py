"""Core JSON type aliases, fast structural cloning, strict equality, and canonical hashing."""

from __future__ import annotations

import dataclasses
import enum
import math
from collections.abc import Hashable, Mapping
from typing import Any

type JsonScalar = None | bool | int | float | str
type JsonArray = list[JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonValue = JsonScalar | JsonArray | JsonObject


def clone_json[T: JsonValue](value: T) -> T:
    """Perform a fast deep copy of a JSON-compatible value without ``copy.deepcopy`` overhead."""
    if isinstance(value, dict):
        return {k: clone_json(v) for k, v in value.items()}  # type: ignore[return-value]
    if isinstance(value, list):
        return [clone_json(item) for item in value]  # type: ignore[return-value]
    return value


def normalize_json(value: Any) -> JsonValue:
    """Normalize rich Python structures (dataclasses, tuples, mappings, enums) into pure JSON values."""
    if isinstance(value, enum.Enum):
        return normalize_json(value.value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Non-finite float {value!r} is not valid in strict JSON documents.")
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: normalize_json(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        result: JsonObject = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"JSON object keys must be str, got {type(k).__name__}.")
            result[k] = normalize_json(v)
        return result
    if isinstance(value, (list, tuple)):
        return [normalize_json(item) for item in value]
    raise TypeError(f"Value of type {type(value).__name__!r} is not JSON-serializable.")


def json_equal(a: JsonValue, b: JsonValue, *, float_epsilon: float = 0.0) -> bool:
    """Compare two JSON values using strict RFC 6902 §4.6 JSON type semantics.

    Unlike Python's built-in ``==``, booleans are strictly distinct from numbers
    (``True != 1`` and ``False != 0``), while numerically equal numbers compare equal
    (``1 == 1.0``), optionally within ``float_epsilon``.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and math.isnan(a):
            return isinstance(b, float) and math.isnan(b)
        if isinstance(b, float) and math.isnan(b):
            return False
        if (isinstance(a, float) and math.isinf(a)) or (isinstance(b, float) and math.isinf(b)):
            return a == b
        if float_epsilon > 0.0:
            return abs(a - b) <= float_epsilon
        return a == b

    if type(a) is not type(b):
        return False

    if isinstance(a, list):
        assert isinstance(b, list)
        if len(a) != len(b):
            return False
        return all(
            json_equal(item_a, item_b, float_epsilon=float_epsilon)
            for item_a, item_b in zip(a, b, strict=True)
        )

    if isinstance(a, dict):
        assert isinstance(b, dict)
        if a.keys() != b.keys():
            return False
        return all(
            json_equal(a[key], b[key], float_epsilon=float_epsilon)
            for key in a
        )

    return a == b


def canonical_key(value: JsonValue) -> Hashable:
    """Build a collision-free hashable key consistent with strict ``json_equal`` semantics."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("num", value)
    if isinstance(value, float):
        if math.isnan(value):
            return ("nan",)
        if math.isinf(value):
            return ("inf", value > 0)
        if value.is_integer():
            return ("num", int(value))
        return ("num", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, list):
        return ("arr", tuple(canonical_key(item) for item in value))
    if isinstance(value, dict):
        return (
            "obj",
            tuple((k, canonical_key(value[k])) for k in sorted(value)),
        )
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")
