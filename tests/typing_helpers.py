"""Small helpers for the type checker in the tests."""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def need(value: T | None) -> T:
    """The value, which must be there: the test fails with an assertion when it is None. The type checker then knows that it is not None."""
    assert value is not None
    return value
