from __future__ import annotations

import pytest

from xc_platform.agents.calculator import CalculatorError, calculate, clock_to_seconds


def test_arithmetic_and_functions() -> None:
    assert calculate("(401 - 506.78) / 7")["result"] == pytest.approx(
        -15.111428, abs=1e-5
    )
    assert calculate("mean(1, 2, 3, 4)")["result"] == 2.5
    assert calculate("round(sqrt(2) * 100)")["result"] == 141


def test_clock_strings_become_seconds() -> None:
    result = calculate('"16:36.70" - "15:31"')
    assert result["result"] == pytest.approx(65.7)
    assert result["as_clock_if_seconds"] == "1:05.70"
    assert clock_to_seconds("1:02:03") == 3723


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "open('x')",
        "(1).__class__",
        "[1, 2][0]",
        "lambda: 1",
        "10 ** 1000",
        "1 / 0",
        "x + 1",
    ],
)
def test_anything_but_arithmetic_is_refused(expression: str) -> None:
    with pytest.raises(CalculatorError):
        calculate(expression)
