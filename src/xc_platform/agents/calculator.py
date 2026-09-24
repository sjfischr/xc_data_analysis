"""Deterministic arithmetic for the analytics agent (Task 19.2).

The model must never do arithmetic in its head (time gaps, averages,
percent changes). This evaluates an expression by walking its parsed AST
and allowing only numbers, arithmetic operators, and a short list of math
functions -- nothing is ever passed to ``eval``, so an expression cannot
reach names, attributes, calls outside the allowlist, or the interpreter.

Clock times are accepted as literals in quotes, e.g. ``"16:36.70" -
"15:31"`` -- converted to seconds before evaluation, so the model never has
to convert them itself.
"""

from __future__ import annotations

import ast
import math
import operator
import re
import statistics
from collections.abc import Callable
from typing import Any

MAX_EXPRESSION_LENGTH = 500
MAX_EXPONENT = 100

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _mean(*values: float) -> float:
    return statistics.fmean(values)


def _median(*values: float) -> float:
    return float(statistics.median(values))


def _stdev(*values: float) -> float:
    return statistics.stdev(values)


_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "mean": _mean,
    "median": _median,
    "stdev": _stdev,
    "sum": lambda *v: math.fsum(v),
}
_CONSTANTS = {"pi": math.pi, "e": math.e}

_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


class CalculatorError(ValueError):
    pass


def clock_to_seconds(text: str) -> float:
    match = _CLOCK_RE.match(text.strip())
    if not match:
        raise CalculatorError(f"not a clock time (m:ss or h:mm:ss): {text!r}")
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)


def seconds_to_clock(seconds: float) -> str:
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    body = (
        f"{minutes:d}:{secs:05.2f}"
        if not hours
        else f"{hours}:{minutes:02d}:{secs:05.2f}"
    )
    return sign + body


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise CalculatorError("booleans are not numbers here")
        if isinstance(node.value, int | float):
            return node.value
        if isinstance(node.value, str):
            return clock_to_seconds(node.value)
        raise CalculatorError(f"unsupported literal: {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculatorError("exponent too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        return _FUNCTIONS[node.func.id](*(_evaluate(a) for a in node.args))
    if isinstance(node, ast.List | ast.Tuple):
        raise CalculatorError("pass values as separate arguments, e.g. mean(1, 2, 3)")
    raise CalculatorError(f"unsupported expression element: {type(node).__name__}")


def calculate(expression: str) -> dict[str, Any]:
    """Evaluate ``expression``. Returns the numeric result and, because
    the platform deals in race times, the same value formatted as a clock
    time (interpreting the result as seconds)."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError("expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"could not parse expression: {exc.msg}") from exc
    try:
        value = _evaluate(tree)
    except ZeroDivisionError as exc:
        raise CalculatorError("division by zero") from exc
    except (OverflowError, ValueError, TypeError) as exc:
        raise CalculatorError(str(exc)) from exc
    result = float(value)
    return {
        "expression": expression,
        "result": round(result, 6),
        "as_clock_if_seconds": seconds_to_clock(result),
    }
