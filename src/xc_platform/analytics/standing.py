"""Standing in the field, and projections of it (Task 19.2 follow-up,
2026-09-24).

**Why standing, not time.** Course distances are fixed (Frosh 2 km, JV 3 km,
Varsity 4 km -- owner-confirmed), yet course and weather conditions move
whole fields: the 2025 JV boys' *median* pace went 10:06 -> 8:17 -> 6:58
per mile across Meets 1-3. A within-season drop in time is mostly that
field-wide swing, so the agent's first projection (a 3:56 mile for a 6th
grader, from extrapolating one boy's three 2025 times) measured the course,
not the runner. Owner decision: "improve" means standing in the field
unless time is explicitly asked about.

**Standing** of a result: the share of the field it beat (``percentile``,
0-100, higher is better), plus its time as a ratio of the race median
(``ratio_to_median``, lower is better). Both cancel conditions shared by
everyone in the race.

**Projection model** (``MODEL_VERSION``), chosen by leave-one-season-out
backtests on 2023-2025 (``backtest``):

* Take the 80 athletes in other seasons whose standing at the starting meet
  was nearest this athlete's, and look at how their standing actually
  changed by the target meet. The projection is the current standing plus
  their median change; the 80% range is their 10th-90th percentile change.
* **What the backtest says, plainly:** the typical miss is about 10
  percentile points overall (8.4-9.8 depending on the meets), a little
  better than guessing "no change", and about 3.5 points for the top 5% of
  a field, whose standing is very sticky (past Meet 1 winners finished
  1st-3rd at Meet 3 every time). The model's value is calibrated
  uncertainty: backtested 80% ranges held 79-80% of outcomes overall and
  93-100% for the top 5%.
* Two earlier versions were tried and dropped. A straight-line regression
  on time-vs-median looked better than "no change" in time but worse in
  standing (whole fields bunch up between meets). A straight-line
  regression on percentile was pulled down by mid-pack noise and badly
  underrated leaders (it put a Meet 1 winner at about 9th).
* Percentiles are bounded, so no projection can be physically impossible.
* Probabilities (hold or improve, finish ahead of, gain more ground) come
  from resampling the neighbors' changes with a fixed seed, so answers
  repeat.

Pure Python on purpose: this runs inside the slim agent image, which has no
numpy.
"""

from __future__ import annotations

import bisect
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from xc_platform.db.repositories.canonical import (
    CanonicalReadRepository,
    ResultRowRecord,
)

MODEL_VERSION = "standing-neighbor-change-v1"
NEIGHBORS = 80
MIN_TRAINING_PAIRS = 100
SIMULATIONS = 4000
SEED = 20260924
MODEL_DIVISIONS = ("Frosh", "JV", "Varsity")


@dataclass(frozen=True, slots=True)
class RaceStanding:
    season_year: int
    meet_number: int | None
    division_code: str
    gender_code: str
    race_id: str
    place_overall: int | None
    finish_time_ms: int
    field_size: int
    ratio_to_median: float
    percentile: float  # share of the field this result beat, 0-100


@dataclass
class Field:
    """Every timed result's standing, indexed by race and athlete."""

    by_race: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    by_athlete: dict[str, list[RaceStanding]] = field(default_factory=dict)


def percentile_in(field_ratios: list[float], ratio: float) -> float:
    """Share (0-100) of ``field_ratios`` slower than ``ratio``, excluding
    the result itself when it is part of the field."""
    slower = sum(1 for x in field_ratios if x > ratio)
    others = max(len(field_ratios) - 1, 1)
    return round(100.0 * min(slower, others) / others, 1)


def build_field(rows: list[ResultRowRecord]) -> Field:
    timed = [r for r in rows if r.finish_time_ms]
    times_by_race: dict[str, list[int]] = defaultdict(list)
    for r in timed:
        times_by_race[r.race_id].append(int(r.finish_time_ms or 0))
    medians = {race: statistics.median(t) for race, t in times_by_race.items()}
    result = Field()
    ratios_by_race: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in timed:
        ratios_by_race[r.race_id].append(
            (r.athlete_id, int(r.finish_time_ms or 0) / medians[r.race_id])
        )
    for race, entries in ratios_by_race.items():
        result.by_race[race] = sorted(entries, key=lambda e: e[1])
    by_athlete: dict[str, list[RaceStanding]] = defaultdict(list)
    for r in timed:
        ratio = int(r.finish_time_ms or 0) / medians[r.race_id]
        field_ratios = [x for _, x in result.by_race[r.race_id]]
        by_athlete[r.athlete_id].append(
            RaceStanding(
                season_year=r.season_year,
                meet_number=r.meet_number,
                division_code=r.division_code,
                gender_code=r.gender_code,
                race_id=r.race_id,
                place_overall=r.place_overall,
                finish_time_ms=int(r.finish_time_ms or 0),
                field_size=len(field_ratios),
                ratio_to_median=ratio,
                percentile=percentile_in(field_ratios, ratio),
            )
        )
    for standings in by_athlete.values():
        standings.sort(key=lambda s: (s.season_year, s.meet_number or 0))
    result.by_athlete = dict(by_athlete)
    return result


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _clamp(percentile: float) -> float:
    return min(max(percentile, 0.0), 100.0)


def place_for(percentile: float, field_size: int) -> int:
    """The place a given percentile corresponds to in a field this size."""
    return 1 + round((100.0 - percentile) / 100.0 * max(field_size - 1, 0))


@dataclass(frozen=True, slots=True)
class StandingModel:
    training_pairs: int
    training_seasons: tuple[int, ...]
    # (starting percentile, later percentile) sorted by start.
    pairs: tuple[tuple[float, float], ...]

    def neighbor_changes(self, percentile_now: float) -> list[float]:
        """How standing changed for the NEIGHBORS nearest starters."""
        starts = [x for x, _ in self.pairs]
        center = bisect.bisect_left(starts, percentile_now)
        low, high = center, center
        while high - low < min(NEIGHBORS, len(self.pairs)):
            take_low = low > 0 and (
                high >= len(starts)
                or percentile_now - starts[low - 1] <= starts[high] - percentile_now
            )
            if take_low:
                low -= 1
            else:
                high += 1
        return [later - start for start, later in self.pairs[low:high]]

    def project(self, percentile_now: float) -> tuple[float, float, float]:
        """(expected, low, high) percentile; low/high bound the 80% range."""
        changes = self.neighbor_changes(percentile_now)
        return (
            _clamp(percentile_now + statistics.median(changes)),
            _clamp(percentile_now + _quantile(changes, 0.1)),
            _clamp(percentile_now + _quantile(changes, 0.9)),
        )


def _meet_pairs(
    fld: Field, *, from_meet: int, to_meet: int, seasons: set[int]
) -> list[tuple[RaceStanding, RaceStanding]]:
    """(earlier, later) standings for every athlete who ran both meets in
    the same season and division."""
    pairs = []
    for standings in fld.by_athlete.values():
        by_key: dict[tuple[int, str], dict[int, RaceStanding]] = defaultdict(dict)
        for s in standings:
            if (
                s.season_year in seasons
                and s.meet_number is not None
                and s.division_code in MODEL_DIVISIONS
            ):
                by_key[(s.season_year, s.division_code)][s.meet_number] = s
        for meets in by_key.values():
            if from_meet in meets and to_meet in meets:
                pairs.append((meets[from_meet], meets[to_meet]))
    return pairs


def training_pairs(
    fld: Field, *, from_meet: int, to_meet: int, seasons: set[int]
) -> list[tuple[float, float]]:
    return [
        (a.percentile, b.percentile)
        for a, b in _meet_pairs(
            fld, from_meet=from_meet, to_meet=to_meet, seasons=seasons
        )
    ]


def fit(
    pairs: list[tuple[float, float]], seasons: tuple[int, ...]
) -> StandingModel | None:
    if len(pairs) < MIN_TRAINING_PAIRS:
        return None
    return StandingModel(
        training_pairs=len(pairs),
        training_seasons=seasons,
        pairs=tuple(sorted(pairs)),
    )


@dataclass(frozen=True, slots=True)
class BacktestSeason:
    """How the model did on one season it never saw, in percentile points
    (share of the field beaten)."""

    test_season: int
    athletes: int
    typical_miss_percentile_points: float
    no_change_guess_typical_miss_percentile_points: float
    range_80_held_share: float


def backtest(
    fld: Field, *, from_meet: int, to_meet: int, seasons: list[int]
) -> list[BacktestSeason]:
    """Leave-one-season-out: fit on the other seasons, predict this one."""
    out = []
    for test in seasons:
        train_seasons = tuple(s for s in seasons if s != test)
        model = fit(
            training_pairs(
                fld, from_meet=from_meet, to_meet=to_meet, seasons=set(train_seasons)
            ),
            train_seasons,
        )
        test_pairs = training_pairs(
            fld, from_meet=from_meet, to_meet=to_meet, seasons={test}
        )
        if model is None or not test_pairs:
            continue
        misses, baseline, covered = [], [], 0
        for now, later in test_pairs:
            expected, low, high = model.project(now)
            covered += low <= later <= high
            misses.append(abs(later - expected))
            baseline.append(abs(later - now))
        out.append(
            BacktestSeason(
                test_season=test,
                athletes=len(test_pairs),
                typical_miss_percentile_points=round(statistics.fmean(misses), 1),
                no_change_guess_typical_miss_percentile_points=round(
                    statistics.fmean(baseline), 1
                ),
                range_80_held_share=round(covered / len(test_pairs), 2),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class AthleteProjection:
    athlete_id: str
    athlete_name: str
    from_meet: int
    to_meet: int
    division_code: str
    current: RaceStanding
    already_raced: RaceStanding | None
    expected_percentile: float
    percentile_range_80: tuple[float, float]
    expected_place: int
    place_range_80: tuple[int, int]
    probability_holds_or_improves: float
    history: list[RaceStanding]


@dataclass
class ProjectionResult:
    season_year: int
    to_meet: int
    model_version: str
    model: StandingModel | None
    athletes: list[AthleteProjection]
    unavailable: dict[str, str]
    head_to_head: list[dict[str, object]]
    backtest: list[BacktestSeason]


def project_standing(
    canonical: CanonicalReadRepository,
    athlete_ids: list[str],
    *,
    season_year: int,
    to_meet: int = 3,
) -> ProjectionResult:
    fld = build_field(canonical.list_result_rows())
    seasons = sorted({s.season_year for v in fld.by_athlete.values() for s in v})
    history_seasons = tuple(s for s in seasons if s != season_year)
    names: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    starts: dict[str, RaceStanding] = {}
    for athlete_id in dict.fromkeys(athlete_ids):
        athlete = canonical.get_athlete(athlete_id)
        if athlete is None:
            unavailable[athlete_id] = "no athlete with that id"
            continue
        names[athlete_id] = athlete.display_name
        season = [
            s
            for s in fld.by_athlete.get(athlete_id, [])
            if s.season_year == season_year
            and s.meet_number is not None
            and s.meet_number < to_meet
        ]
        if not season:
            unavailable[athlete_id] = (
                f"no {season_year} result before Meet {to_meet} to project from"
            )
            continue
        starts[athlete_id] = season[-1]

    projections: list[AthleteProjection] = []
    draws: dict[str, tuple[float, list[float]]] = {}
    model: StandingModel | None = None
    for athlete_id, start in starts.items():
        from_meet = int(start.meet_number or 0)
        model = fit(
            training_pairs(
                fld, from_meet=from_meet, to_meet=to_meet, seasons=set(history_seasons)
            ),
            history_seasons,
        )
        if model is None:
            unavailable[athlete_id] = (
                f"not enough history for Meet {from_meet} -> Meet {to_meet}"
            )
            continue
        now = start.percentile
        expected, low, high = model.project(now)
        changes = model.neighbor_changes(now)
        # Seeded per athlete and meet, so an athlete's numbers are identical
        # whichever other athletes are in the same question.
        rng = random.Random(f"{SEED}:{athlete_id}:{season_year}:{to_meet}")  # noqa: S311 -- reproducible simulation
        simulated = [_clamp(now + rng.choice(changes)) for _ in range(SIMULATIONS)]
        draws[athlete_id] = (now, simulated)
        # "Holds" allows half a place of slack, so a race winner who wins
        # again counts as holding.
        tolerance = 50.0 / max(start.field_size - 1, 1)
        actual = next(
            (
                s
                for s in fld.by_athlete[athlete_id]
                if s.season_year == season_year and s.meet_number == to_meet
            ),
            None,
        )
        projections.append(
            AthleteProjection(
                athlete_id=athlete_id,
                athlete_name=names[athlete_id],
                from_meet=from_meet,
                to_meet=to_meet,
                division_code=start.division_code,
                current=start,
                already_raced=actual,
                expected_percentile=round(expected, 1),
                percentile_range_80=(round(low, 1), round(high, 1)),
                expected_place=place_for(expected, start.field_size),
                place_range_80=(
                    place_for(high, start.field_size),
                    place_for(low, start.field_size),
                ),
                probability_holds_or_improves=round(
                    sum(1 for p in simulated if p >= now - tolerance) / SIMULATIONS, 3
                ),
                history=fld.by_athlete[athlete_id],
            )
        )

    head_to_head = []
    ids = [p.athlete_id for p in projections]
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            now_a, sim_a = draws[a]
            now_b, sim_b = draws[b]
            pairs = list(zip(sim_a, sim_b, strict=True))
            head_to_head.append(
                {
                    "athlete_a": names[a],
                    "athlete_b": names[b],
                    "probability_a_finishes_ahead": round(
                        sum(1 for x, y in pairs if x > y) / SIMULATIONS, 3
                    ),
                    "probability_a_gains_more_ground": round(
                        sum(1 for x, y in pairs if (x - now_a) > (y - now_b))
                        / SIMULATIONS,
                        3,
                    ),
                }
            )

    from_meets = sorted({p.from_meet for p in projections}) or [1]
    return ProjectionResult(
        season_year=season_year,
        to_meet=to_meet,
        model_version=MODEL_VERSION,
        model=model,
        athletes=projections,
        unavailable=unavailable,
        head_to_head=head_to_head,
        backtest=backtest(
            fld,
            from_meet=from_meets[0],
            to_meet=to_meet,
            seasons=list(history_seasons),
        ),
    )
