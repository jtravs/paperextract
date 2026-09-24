"""Validate decoded JSON fields at persistence boundaries.

Every persisted schema in this project is parsed with these small checks so
that malformed or foreign documents fail loudly instead of being reinterpreted.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import cast

__all__ = [
    "boolean",
    "choice",
    "dump",
    "integer",
    "items",
    "mapping",
    "number",
    "record",
    "string",
    "text",
]


def record(value: object, keys: str) -> dict[str, object]:
    """Require a JSON object with exactly the fields of a schema version.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    keys : str
        Space-separated required field names.

    Returns
    -------
    dict of str to object
        Validated object with exactly the required fields.
    """
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    result = cast("dict[str, object]", value)
    if set(result) != set(keys.split()):
        raise ValueError(f"Expected exactly these fields: {keys}")
    return result


def string(value: object) -> str:
    """Require a non-blank string.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    str
        Validated string, unchanged.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a string with content")
    return value


def text(value: object) -> str:
    """Require a string, allowing an empty one.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    str
        Validated string, unchanged.
    """
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def integer(value: object, *, minimum: int) -> int:
    """Require an integer at or above a bound, rejecting booleans.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    minimum : int
        Smallest accepted value.

    Returns
    -------
    int
        Validated integer.
    """
    if type(value) is not int or value < minimum:
        raise ValueError(f"Expected an integer of at least {minimum}")
    return value


def number(value: object) -> float:
    """Require a finite JSON number, rejecting booleans.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    float
        The number as a float.
    """
    if type(value) not in (int, float):
        raise ValueError("Expected a number")
    result = float(cast("int | float", value))
    if not math.isfinite(result):
        raise ValueError("Expected a finite number")
    return result


def boolean(value: object) -> bool:
    """Require a JSON boolean.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    bool
        Validated boolean.
    """
    if type(value) is not bool:
        raise ValueError("Expected a boolean")
    return value


def choice(value: object, choices: str) -> str:
    """Restrict a persisted vocabulary field to the supported values.

    Parameters
    ----------
    value : object
        Decoded JSON value.
    choices : str
        Space-separated allowed values.

    Returns
    -------
    str
        Validated vocabulary member.
    """
    result = string(value)
    if result not in choices.split():
        raise ValueError(f"Expected one of: {choices}")
    return result


def items(value: object) -> list[object]:
    """Require a JSON array.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    list of object
        Validated array.
    """
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return cast("list[object]", value)


def mapping(value: object) -> dict[str, object]:
    """Require a JSON object with arbitrary fields.

    Parameters
    ----------
    value : object
        Decoded JSON value.

    Returns
    -------
    dict of str to object
        Shallow copy of the object.
    """
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return dict(cast("dict[str, object]", value))


def dump(document: Mapping[str, object]) -> str:
    """Serialize a document deterministically.

    Parameters
    ----------
    document : Mapping of str to object
        JSON-compatible document.

    Returns
    -------
    str
        Indented JSON with sorted keys and a trailing newline.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
