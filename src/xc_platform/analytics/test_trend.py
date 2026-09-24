from __future__ import annotations

from xc_platform.analytics.trend import TREND_IMPLEMENTATION_VERSION, compute_trend


def test_fewer_than_two_points_makes_no_claim() -> None:
    assert compute_trend([]) is None
    assert compute_trend([(2026.0, 400.0)]) is None


def test_two_points_reports_observed_change_without_confidence() -> None:
    result = compute_trend([(2025.0, 420.0), (2026.0, 400.0)])
    assert result is not None
    assert result.sample_size == 2
    assert result.has_confidence is False
    assert result.observed_change == -20.0
    assert result.slope_per_x is None
    assert result.r_squared is None


def test_three_or_more_points_computes_ols_slope_and_fit() -> None:
    # Perfectly linear improvement: pace drops 10s/year.
    points = [(2024.0, 430.0), (2025.0, 420.0), (2026.0, 410.0)]
    result = compute_trend(points)
    assert result is not None
    assert result.sample_size == 3
    assert result.has_confidence is True
    assert result.slope_per_x == -10.0
    assert result.r_squared == 1.0
    assert result.span == 2.0
    assert result.implementation_version == TREND_IMPLEMENTATION_VERSION


def test_noisy_points_have_a_fit_quality_below_one() -> None:
    points = [(2024.0, 430.0), (2025.0, 415.0), (2026.0, 412.0)]
    result = compute_trend(points)
    assert result is not None
    assert result.r_squared is not None
    assert 0.0 < result.r_squared < 1.0


def test_identical_x_values_do_not_divide_by_zero() -> None:
    points = [(2026.0, 400.0), (2026.0, 410.0), (2026.0, 420.0)]
    result = compute_trend(points)
    assert result is not None
    assert result.slope_per_x == 0.0
    assert result.r_squared == 0.0


def test_points_are_sorted_by_x_before_computing() -> None:
    result = compute_trend([(2026.0, 400.0), (2024.0, 430.0), (2025.0, 415.0)])
    assert result is not None
    assert result.first_x == 2024.0
    assert result.last_x == 2026.0
