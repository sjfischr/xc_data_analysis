from __future__ import annotations

import pytest

from xc_platform.analytics.charts import (
    ChartValidationError,
    build_comparison_bar_chart,
    build_progression_chart,
    validate_chart_spec,
)


def test_progression_chart_builds_a_valid_inline_spec() -> None:
    rows = [{"season_year": 2024, "pace": 430.0}, {"season_year": 2025, "pace": 410.0}]
    chart = build_progression_chart(
        title="Pace over time",
        description="Jane Doe's pace per mile by season.",
        x_field="season_year",
        x_title="Season",
        y_field="pace",
        y_title="Pace (s/mile)",
        rows=rows,
        publication_id="pub-1",
    )
    assert chart.spec["data"]["values"] == rows
    assert chart.spec["mark"]["type"] == "line"
    assert chart.fallback_columns == ["season_year", "pace"]
    assert chart.publication_id == "pub-1"
    assert "url" not in str(chart.spec)


def test_comparison_bar_chart_builds_a_valid_inline_spec() -> None:
    rows = [{"school": "St Agnes", "score": 42}, {"school": "Holy Family", "score": 51}]
    chart = build_comparison_bar_chart(
        title="Team scores",
        description="Team score comparison.",
        category_field="school",
        category_title="School",
        value_field="score",
        value_title="Score",
        rows=rows,
        publication_id="pub-1",
    )
    assert chart.spec["mark"] == "bar"
    assert chart.data == rows


def test_validate_chart_spec_rejects_a_disallowed_mark() -> None:
    spec = {"title": "t", "mark": "geoshape", "data": {"values": []}}
    violations = validate_chart_spec(spec, row_count=0)
    assert any("geoshape" in v for v in violations)


def test_validate_chart_spec_rejects_a_remote_url() -> None:
    spec = {
        "title": "t",
        "mark": "line",
        "data": {"url": "https://example.com/data.json"},
    }
    violations = validate_chart_spec(spec, row_count=0)
    assert any("remote URL" in v for v in violations)


def test_validate_chart_spec_rejects_an_expr_transform() -> None:
    spec = {
        "title": "t",
        "mark": "line",
        "data": {"values": []},
        "transform": [{"calculate": "expr(datum.x)", "as": "y", "expr": "datum.x * 2"}],
    }
    violations = validate_chart_spec(spec, row_count=0)
    assert any("expression" in v for v in violations)


def test_validate_chart_spec_rejects_missing_title_and_description() -> None:
    spec = {"mark": "line", "data": {"values": []}}
    violations = validate_chart_spec(spec, row_count=0)
    assert any("accessibility" in v for v in violations)


def test_validate_chart_spec_rejects_oversized_data() -> None:
    spec = {"title": "t", "mark": "line", "data": {"values": []}}
    violations = validate_chart_spec(spec, row_count=10_000)
    assert any("exceeds" in v for v in violations)


def test_build_progression_chart_raises_on_a_row_cap_violation() -> None:
    rows = [{"season_year": i, "pace": 400.0} for i in range(600)]
    with pytest.raises(ChartValidationError):
        build_progression_chart(
            title="t",
            description="d",
            x_field="season_year",
            x_title="Season",
            y_field="pace",
            y_title="Pace",
            rows=rows,
            publication_id="pub-1",
        )
