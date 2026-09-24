"""Reject malformed JSON field values at persistence boundaries."""

import math

import pytest

from paperextract.fields import boolean, dump, integer, number, record, text


@pytest.mark.parametrize("value", [float("nan"), math.inf, -math.inf, True, "1"])
def test_number_rejects_non_finite_and_non_numeric_values(value: object) -> None:
    with pytest.raises(ValueError):
        number(value)


def test_helpers_accept_valid_values() -> None:
    assert number(3) == 3.0
    assert integer(2, minimum=1) == 2
    assert boolean(False) is False
    assert text("") == ""
    assert record({"a": 1}, "a") == {"a": 1}
    assert (
        dump({"b": 1, "a": [1, 2]}) == '{\n  "a": [\n    1,\n    2\n  ],\n  "b": 1\n}\n'
    )
