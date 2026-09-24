"""Constrained Vega-Lite chart construction for the analytics agent
(Task 19.2, design.md 11.1's ``build_chart_spec``).

The model never writes a Vega-Lite spec itself. It chooses a chart type,
fields, and axis semantics; this module validates the data and assembles
the spec. So a chart can only ever contain the data the model passed from
tool results, rendered with one of a few known shapes -- no arbitrary
spec keys, URLs, signals, or expressions reach the browser (the browser
also renders with vega's AST interpreter, web/components/VegaChart.tsx).
"""

from __future__ import annotations

from typing import Any

CHART_TYPES = ("line", "bar", "scatter", "area")
AXIS_FORMATS = ("number", "clock_seconds", "pace_seconds_per_mile", "place")
MAX_ROWS = 500
MAX_FIELDS = 8
MAX_STRING = 120

_CLOCK_LABEL = (
    "floor(datum.value / 60) + ':' + (datum.value % 60 < 10 ? '0' : '') "
    "+ floor(datum.value % 60)"
)


class ChartSpecError(ValueError):
    pass


def _clean_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return value[:MAX_STRING]
    raise ChartSpecError(
        f"chart data values must be numbers or strings, got {type(value).__name__}"
    )


def build_chart_spec(
    *,
    chart_type: str,
    title: str,
    data: list[dict[str, Any]],
    x: str,
    y: str,
    color: str | None = None,
    x_is_category: bool = True,
    y_format: str = "number",
    y_title: str | None = None,
    x_title: str | None = None,
) -> dict[str, Any]:
    if chart_type not in CHART_TYPES:
        raise ChartSpecError(f"chart_type must be one of {CHART_TYPES}")
    if y_format not in AXIS_FORMATS:
        raise ChartSpecError(f"y_format must be one of {AXIS_FORMATS}")
    if not data:
        raise ChartSpecError("data is empty -- nothing to chart")
    if len(data) > MAX_ROWS:
        raise ChartSpecError(f"at most {MAX_ROWS} rows per chart; aggregate first")

    fields = {x, y} | ({color} if color else set())
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ChartSpecError("each data row must be an object")
        missing = fields - row.keys()
        if missing:
            raise ChartSpecError(f"row {i} is missing field(s): {sorted(missing)}")
        kept = {k: _clean_value(v) for k, v in list(row.items())[:MAX_FIELDS]}
        for f in fields:
            kept[f] = _clean_value(row[f])
        kept["_order"] = i
        rows.append(kept)

    reverse = y_format in ("clock_seconds", "pace_seconds_per_mile", "place")
    y_axis: dict[str, Any] = {"tickCount": 6}
    if y_format in ("clock_seconds", "pace_seconds_per_mile"):
        y_axis["labelExpr"] = _CLOCK_LABEL
    if y_format == "place":
        y_axis["tickMinStep"] = 1
        y_axis["format"] = "d"

    x_enc: dict[str, Any] = {
        "field": x,
        "type": "ordinal" if x_is_category else "quantitative",
        "title": x_title if x_title is not None else x,
    }
    if x_is_category:
        x_enc["sort"] = {"field": "_order"}
        x_enc["axis"] = {"labelAngle": -35 if len(rows) > 6 else 0, "labelLimit": 140}
    y_enc: dict[str, Any] = {
        "field": y,
        "type": "quantitative",
        "title": y_title if y_title is not None else y,
        "scale": {
            "zero": chart_type in ("bar", "area") and not reverse,
            "reverse": reverse,
        },
        "axis": y_axis,
    }
    tooltip = [
        {
            "field": k,
            "type": "quantitative"
            if isinstance(rows[0].get(k), int | float)
            else "nominal",
        }
        for k in rows[0]
        if k != "_order"
    ]
    marks: dict[str, dict[str, Any]] = {
        "line": {"type": "line", "point": {"filled": True, "size": 60}},
        "bar": {"type": "bar", "cornerRadiusEnd": 3},
        "scatter": {"type": "point", "filled": True, "size": 80},
        "area": {"type": "area", "opacity": 0.7, "line": True},
    }
    mark = marks[chart_type]
    encoding: dict[str, Any] = {"x": x_enc, "y": y_enc, "tooltip": tooltip}
    if color:
        encoding["color"] = {"field": color, "type": "nominal", "title": color}
    elif chart_type in ("line", "bar", "area", "scatter"):
        mark["color"] = "var(--chart-1)"

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "title": title[:MAX_STRING],
        "data": {"values": rows},
        "mark": mark,
        "encoding": encoding,
    }
