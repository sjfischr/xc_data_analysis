"""Versioned pure-Python trend computation (design.md section 11.2).

No numpy/scipy: this project's new platform code is stdlib-first
(docs/dependency-review.md), and design.md explicitly calls for "a versioned
pure-Python implementation" here specifically -- not just as a style
preference but because the analytics agent must be able to cite an exact,
reproducible implementation version alongside any trend claim it makes
(Requirement 10.5).

Statistical safeguards (design.md 11.2), enforced as the return shape, not
left to callers to remember:

* fewer than two points: no improvement claim -- :func:`compute_trend`
  returns ``None``.
* exactly two points: an observed change only, with ``has_confidence=False``
  and no slope/fit-quality fields populated.
* three or more points: ordinary least squares, with slope, an R^2 fit
  quality, sample size, and span.
"""

from __future__ import annotations

from dataclasses import dataclass

TREND_IMPLEMENTATION_VERSION = "trend-ols-v1"


@dataclass(frozen=True, slots=True)
class TrendResult:
    implementation_version: str
    sample_size: int
    has_confidence: bool
    first_x: float
    last_x: float
    first_y: float
    last_y: float
    observed_change: float
    slope_per_x: float | None = None
    r_squared: float | None = None
    span: float | None = None


def compute_trend(points: list[tuple[float, float]]) -> TrendResult | None:
    """``points`` are ``(x, y)`` pairs, e.g. ``(season_year, pace_seconds)``,
    already ordered by ``x``. Outliers are identified by the caller (via
    ``r_squared``), never silently dropped here (design.md 11.2)."""
    if len(points) < 2:
        return None

    ordered = sorted(points, key=lambda p: p[0])
    first_x, first_y = ordered[0]
    last_x, last_y = ordered[-1]
    observed_change = last_y - first_y

    if len(ordered) == 2:
        return TrendResult(
            implementation_version=TREND_IMPLEMENTATION_VERSION,
            sample_size=2,
            has_confidence=False,
            first_x=first_x,
            last_x=last_x,
            first_y=first_y,
            last_y=last_y,
            observed_change=observed_change,
        )

    n = len(ordered)
    mean_x = sum(x for x, _ in ordered) / n
    mean_y = sum(y for _, y in ordered) / n
    ss_xx = sum((x - mean_x) ** 2 for x, _ in ordered)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in ordered)
    ss_yy = sum((y - mean_y) ** 2 for _, y in ordered)

    if ss_xx == 0:
        # Every x is identical (e.g. same season): no meaningful slope.
        slope = 0.0
        r_squared = 0.0
    else:
        slope = ss_xy / ss_xx
        if ss_yy == 0:
            r_squared = 1.0
        else:
            r_squared = (ss_xy**2) / (ss_xx * ss_yy)

    return TrendResult(
        implementation_version=TREND_IMPLEMENTATION_VERSION,
        sample_size=n,
        has_confidence=True,
        first_x=first_x,
        last_x=last_x,
        first_y=first_y,
        last_y=last_y,
        observed_change=observed_change,
        slope_per_x=slope,
        r_squared=r_squared,
        span=last_x - first_x,
    )
