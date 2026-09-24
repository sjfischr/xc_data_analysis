from __future__ import annotations

import pytest

from xc_platform.agents.chart_spec import ChartSpecError, build_chart_spec


def test_pace_line_chart_reverses_the_axis_and_formats_clock_labels() -> None:
    spec = build_chart_spec(
        chart_type="line",
        title="Pace",
        data=[{"race": "M1", "pace": 420.0}, {"race": "M2", "pace": 410.5}],
        x="race",
        y="pace",
        y_format="pace_seconds_per_mile",
    )
    assert spec["mark"]["type"] == "line"
    assert spec["encoding"]["y"]["scale"]["reverse"] is True
    assert "labelExpr" in spec["encoding"]["y"]["axis"]
    assert [row["_order"] for row in spec["data"]["values"]] == [0, 1]


def test_only_known_keys_reach_the_spec() -> None:
    spec = build_chart_spec(
        chart_type="bar",
        title="t",
        data=[{"a": "x", "b": 1}],
        x="a",
        y="b",
    )
    assert set(spec) == {"$schema", "title", "data", "mark", "encoding"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"chart_type": "pie"}, "chart_type"),
        ({"data": []}, "empty"),
        ({"data": [{"a": "x"}]}, "missing"),
        ({"data": [{"a": "x", "b": {"nested": 1}}]}, "numbers or strings"),
        ({"y_format": "percent"}, "y_format"),
    ],
)
def test_invalid_requests_are_refused(kwargs: dict[str, object], message: str) -> None:
    base: dict[str, object] = {
        "chart_type": "bar",
        "title": "t",
        "data": [{"a": "x", "b": 1}],
        "x": "a",
        "y": "b",
    }
    with pytest.raises(ChartSpecError, match=message):
        build_chart_spec(**{**base, **kwargs})  # type: ignore[arg-type]
