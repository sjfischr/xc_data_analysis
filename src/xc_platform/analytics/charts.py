"""Declarative chart generation and validation (Task 11.2, design.md
section 11.4).

The agent never returns executable client code (Requirement 10.7) -- only a
validated Vega-Lite spec plus its bounded inline data and an equivalent
tabular fallback (Requirement 12.5). :func:`validate_chart_spec` is the
server-side enforcement design.md 11.4 requires before any chart response
leaves the server: permitted marks only, inline data only (no remote
``"url"``), no ``"expr"``/dynamic-signal transforms, and a row cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHART_SCHEMA_VERSION = "https://vega.github.io/schema/vega-lite/v5.json"

# Marks this project's templates use. Anything else (e.g. "geoshape",
# "trail") is refused -- there is no legitimate chart type in this domain
# that needs them, and a smaller allowlist is a smaller attack surface.
ALLOWED_MARKS: frozenset[str] = frozenset({"line", "bar", "point", "area", "rule"})

MAX_CHART_ROWS = 500


class ChartValidationError(RuntimeError):
    """Raised with every violation found, not just the first."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


@dataclass(frozen=True, slots=True)
class ChartResponse:
    schema_version: str
    title: str
    description: str
    spec: dict[str, Any]
    data: list[dict[str, Any]]
    fallback_columns: list[str]
    publication_id: str
    metric_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "description": self.description,
            "spec": self.spec,
            "data": self.data,
            "fallback_columns": self.fallback_columns,
            "publication_id": self.publication_id,
            "metric_versions": self.metric_versions,
        }


def _find_marks(node: Any, marks: list[str]) -> None:
    if isinstance(node, dict):
        mark = node.get("mark")
        if isinstance(mark, str):
            marks.append(mark)
        elif isinstance(mark, dict) and isinstance(mark.get("type"), str):
            marks.append(mark["type"])
        for value in node.values():
            _find_marks(value, marks)
    elif isinstance(node, list):
        for item in node:
            _find_marks(item, marks)


def _contains_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def validate_chart_spec(spec: dict[str, Any], *, row_count: int) -> list[str]:
    """Returns every violation found (empty list means valid). Never
    raises -- callers decide whether to raise, log, or refuse."""
    violations: list[str] = []

    marks: list[str] = []
    _find_marks(spec, marks)
    if not marks:
        violations.append("spec has no mark")
    for mark in marks:
        if mark not in ALLOWED_MARKS:
            violations.append(
                f"mark {mark!r} is not in the allowed set {sorted(ALLOWED_MARKS)}"
            )

    if _contains_key(spec, "url"):
        violations.append("spec references a remote URL; data must be inline")
    if _contains_key(spec, "expr"):
        violations.append("spec uses a Vega expression; not permitted")
    if _contains_key(spec, "init"):
        violations.append(
            "spec defines a signal/selection init expression; not permitted"
        )

    if row_count > MAX_CHART_ROWS:
        violations.append(
            f"{row_count} rows exceeds the {MAX_CHART_ROWS}-row chart cap"
        )

    if not spec.get("title") and not spec.get("description"):
        violations.append("spec has neither a title nor a description (accessibility)")

    return violations


def build_progression_chart(
    *,
    title: str,
    description: str,
    x_field: str,
    x_title: str,
    y_field: str,
    y_title: str,
    rows: list[dict[str, Any]],
    publication_id: str,
    metric_versions: dict[str, str] | None = None,
) -> ChartResponse:
    """A single-series line-and-point chart over time (design.md 11.1's
    "finish-time and place history" / pace-progression charts)."""
    spec: dict[str, Any] = {
        "$schema": CHART_SCHEMA_VERSION,
        "title": title,
        "description": description,
        "data": {"values": rows},
        "mark": {"type": "line", "point": True},
        "encoding": {
            "x": {"field": x_field, "type": "quantitative", "title": x_title},
            "y": {"field": y_field, "type": "quantitative", "title": y_title},
        },
    }
    violations = validate_chart_spec(spec, row_count=len(rows))
    if violations:
        raise ChartValidationError(violations)
    return ChartResponse(
        schema_version=CHART_SCHEMA_VERSION,
        title=title,
        description=description,
        spec=spec,
        data=rows,
        fallback_columns=[x_field, y_field],
        publication_id=publication_id,
        metric_versions=metric_versions or {},
    )


def build_comparison_bar_chart(
    *,
    title: str,
    description: str,
    category_field: str,
    category_title: str,
    value_field: str,
    value_title: str,
    rows: list[dict[str, Any]],
    publication_id: str,
    metric_versions: dict[str, str] | None = None,
) -> ChartResponse:
    """A grouped bar chart for comparing discrete entities (athletes,
    schools, seasons) on one metric (design.md 11.1's head-to-head /
    comparison calculations)."""
    spec: dict[str, Any] = {
        "$schema": CHART_SCHEMA_VERSION,
        "title": title,
        "description": description,
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {
            "x": {"field": category_field, "type": "nominal", "title": category_title},
            "y": {"field": value_field, "type": "quantitative", "title": value_title},
        },
    }
    violations = validate_chart_spec(spec, row_count=len(rows))
    if violations:
        raise ChartValidationError(violations)
    return ChartResponse(
        schema_version=CHART_SCHEMA_VERSION,
        title=title,
        description=description,
        spec=spec,
        data=rows,
        fallback_columns=[category_field, value_field],
        publication_id=publication_id,
        metric_versions=metric_versions or {},
    )
