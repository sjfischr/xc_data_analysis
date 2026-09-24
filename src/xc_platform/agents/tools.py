"""Typed, bounded Strands tools for the analytics agent (Task 11.1,
extended by Task 19.2; design.md section 11.3).

Every data tool is a thin adapter: the logic lives in
:mod:`xc_platform.analytics` (runtime-neutral, no Strands import, tested on
its own). This module binds that logic to one pinned snapshot connection and
publication ID for the lifetime of a session (design.md 12.3) and shapes
results as small JSON-serializable dicts -- never a live database handle.

Order of preference the system prompt teaches, most deterministic first:
typed tools (their math is our tested code) -> ``run_readonly_query_tool``
-> ``calculate_tool`` -> ``run_python_analysis_tool`` (model-written code,
sandboxed). Presentation tools (``build_chart_tool``,
``suggest_follow_ups_tool``) emit UI events through ``emit`` rather than
returning content for the model to repeat.

Found in the 2026-09-23 model evaluation: with no schema in its
description, ``run_readonly_query_tool`` sent Haiku 4.5 *and* Sonnet 4.6
guessing at table names until they hit the turn cap on every aggregation
question; the schema text below took Haiku from 6/9 to 9/9 on gradable
questions. Tool descriptions matter more than model size here.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from collections.abc import Callable
from typing import Any

from strands import tool

from xc_platform.agents.calculator import CalculatorError, calculate
from xc_platform.agents.chart_spec import ChartSpecError, build_chart_spec
from xc_platform.agents.code_interpreter import (
    CHART_FILENAME,
    PythonExecutor,
    image_data_uri,
)
from xc_platform.analytics.catalog import find_athletes, find_schools, list_dimensions
from xc_platform.analytics.dashboard import (
    ResultFilters,
    ResultRow,
    get_athlete_profile,
    get_overview,
    list_results,
)
from xc_platform.analytics.query import (
    DEFAULT_ALLOWED_TABLES,
    DEFAULT_MAX_ROWS,
    DEFAULT_MAX_SECONDS,
    QueryRefusedError,
    run_readonly_query,
)
from xc_platform.analytics.scenarios import (
    HypotheticalRunner,
    compare_athletes,
    team_score_scenario,
)
from xc_platform.analytics.standing import (
    RaceStanding,
    build_field,
    project_standing,
)
from xc_platform.analytics.team_scores import (
    get_saint_sebastian_standings,
    get_team_scores,
)
from xc_platform.db.repositories.canonical import CanonicalReadRepository

EventSink = Callable[[dict[str, Any]], None]

MAX_RESULT_ROWS = 150
MAX_PYTHON_DATA_ROWS = 5000
MAX_FOLLOW_UPS = 4
MAX_PYTHON_CODE_CHARS = 6000

SQL_TOOL_DOC = """Run one bounded read-only SELECT/WITH query (max 200 rows, 5 s)
for questions the typed tools do not cover -- counts, rankings, cross-season
aggregation. Refused if it writes, has more than one statement, or reaches an
unlisted table. sqlite_master is not readable; this is the whole schema:

- meets(meet_id, season_year, meet_number, name, series, meet_date, status)
- races(race_id, meet_id, division_code, gender_code, distance_meters)
  division_code in ('2nd Grade','Frosh','JV','Varsity'); gender_code in ('F','M','X')
- schools(school_id, canonical_name, display_name, status)
  canonical_name 'Unknown' is the placeholder for "no team recorded"
- athletes(athlete_id, display_name, canonical_first_name, canonical_last_name,
  status)  -- filter status = 'active' (merged duplicates are 'merged')
- athlete_seasons(athlete_id, season_year, school_id, grade, gender_code)
  one row per athlete per season: the roster
- results(result_id, race_id, athlete_id, school_id, finish_time_ms,
  place_overall, grade, scored_flag)
Team scores and Saint Sebastian standings are not tables here -- use
get_team_scores_tool / get_saint_sebastian_standings_tool for them.

Joins: results.race_id = races.race_id; races.meet_id = meets.meet_id;
results.athlete_id = athletes.athlete_id; results.school_id = schools.school_id.
Pace in seconds per mile = (finish_time_ms / 1000.0) /
(distance_meters / 1609.344). Lower pace and lower place are better.

Args:
    sql: A single SELECT or WITH statement.
"""


PYTHON_TOOL_DOC = """Run Python in an isolated sandbox for statistics the other
tools can't do -- significance tests, regression, correlation, distributions,
percentiles. NOT for predicting or projecting an athlete's future result:
use project_standing_tool, which is backtested. Never extrapolate times or
fit a trend to one athlete's few races (3 points give a meaningless R^2).
Within-season changes in time are mostly course conditions shared by the
whole field; compare runners against the field (their race's median).
pandas, numpy, scipy, statsmodels, and sympy are installed; use
scipy/statsmodels whenever they fit.

Data gets in ONLY through `sql`: its rows (up to 5000) are written to
data.csv before `code` runs -- read it with pandas.read_csv("data.csv").
Never type data values into the code. Compute derived values (such as pace)
in the SQL or in pandas.

Comparing groups of athletes (grades, schools, genders)? Aggregate to ONE
row per athlete first -- e.g. each athlete's mean or best pace with
GROUP BY athlete_id -- because repeated races by one athlete are not
independent samples. Report n as the number of athletes. Check assumptions
and prefer a non-parametric test (Mann-Whitney U, Kruskal-Wallis) when data
are clearly non-normal. Print paces and times as m:ss (for seconds p:
f"{int(p // 60)}:{int(p % 60):02d}"), never as raw seconds. print() the
results you need; only stdout comes back. The sandbox has no network and no
database. For a chart that
build_chart_tool can't draw (box plot, histogram, regression band), save one
matplotlib figure to "chart.png" and it is shown to the user.

Tables for `sql` (read-only SELECT/WITH; join on the id columns):
- meets(meet_id, season_year, meet_number, name, meet_date)
- races(race_id, meet_id, division_code, gender_code, distance_meters)
  division_code in ('2nd Grade','Frosh','JV','Varsity'); gender_code 'F'/'M'
- schools(school_id, display_name)
- athletes(athlete_id, display_name, status)  -- status = 'active'
- athlete_seasons(athlete_id, season_year, school_id, grade, gender_code)
- results(result_id, race_id, athlete_id, school_id, finish_time_ms,
  place_overall, grade)
Pace in seconds per mile = (finish_time_ms / 1000.0) /
(distance_meters / 1609.344).

Args:
    sql: The SELECT/WITH query whose rows become data.csv.
    code: Python source that reads data.csv and prints results.
"""


def _asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _clock(ms: int | None) -> str | None:
    if ms is None:
        return None
    seconds = ms / 1000
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


def _pace(seconds_per_mile: float | None) -> str | None:
    if seconds_per_mile is None:
        return None
    rounded = round(seconds_per_mile)
    return f"{rounded // 60}:{rounded % 60:02d}/mi"


def _team_summary(entries: list[Any]) -> list[dict[str, Any]]:
    """Per-school team-race counts over the filtered scores, most wins
    first -- so "who won the most team races" is never hand-counted."""
    by_school: dict[str, dict[str, Any]] = {}
    for e in entries:
        row = by_school.setdefault(
            e.school_id,
            {
                "school": e.school_display_name,
                "school_id": e.school_id,
                "team_wins": 0,
                "podiums": 0,
                "races_scored": 0,
                "best_score": None,
            },
        )
        row["races_scored"] += 1
        row["team_wins"] += e.team_rank == 1
        row["podiums"] += e.team_rank <= 3
        if row["best_score"] is None or e.score < row["best_score"]:
            row["best_score"] = e.score
    return sorted(
        by_school.values(),
        key=lambda r: (-r["team_wins"], -r["podiums"], r["school"]),
    )


def _standing_row(st: RaceStanding) -> dict[str, Any]:
    return {
        "season_year": st.season_year,
        "meet_number": st.meet_number,
        "race": f"{st.division_code} {st.gender_code}",
        "place": st.place_overall,
        "field_size": st.field_size,
        "percentile_beaten": st.percentile,
        "ratio_to_race_median": round(st.ratio_to_median, 3),
        "time": _clock(st.finish_time_ms),
    }


def _pct(probability: float) -> str:
    return f"{round(probability * 100)}%"


def _ordinal(n: int) -> str:
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def _likelihood(probability: float) -> str:
    """Plain words, so 46% is never called "unlikely" or 52% an "edge"."""
    if probability >= 0.8:
        return "very likely"
    if probability >= 0.6:
        return "likely"
    if probability > 0.4:
        return "about a coin flip"
    if probability > 0.2:
        return "unlikely"
    return "very unlikely"


def _history_lines(name: str, history: list[RaceStanding]) -> list[str]:
    """One line per season: places and share of field beaten, first to
    last, with the direction stated so it is never misread."""
    lines = []
    seasons = sorted({h.season_year for h in history})
    for season in seasons:
        races = [h for h in history if h.season_year == season]
        steps = ", ".join(
            f"Meet {h.meet_number} "
            + (f"{_ordinal(h.place_overall)} " if h.place_overall else "")
            + f"(beat {round(h.percentile)}%)"
            for h in races
        )
        first, last = races[0], races[-1]
        change = last.percentile - first.percentile
        if len(races) < 2:
            trend = "one race"
        elif first.place_overall and last.place_overall:
            if last.place_overall < first.place_overall:
                trend = (
                    f"moved up from {_ordinal(first.place_overall)} to "
                    f"{_ordinal(last.place_overall)}"
                )
            elif last.place_overall > first.place_overall:
                trend = (
                    f"slipped from {_ordinal(first.place_overall)} to "
                    f"{_ordinal(last.place_overall)}"
                )
            else:
                trend = f"held {_ordinal(first.place_overall)}"
        elif abs(change) < 2:
            trend = "held steady"
        else:
            trend = (
                f"{'gained' if change > 0 else 'slipped'} "
                f"{abs(round(change))} points in standing"
            )
        lines.append(
            f"History, {name}, {season} {races[0].division_code}: {steps} -- {trend}."
        )
    return lines


def _projection_sentences(result: Any) -> list[str]:
    """Plain sentences for the facts an answer must state correctly, with a
    deterministic bottom line first. Found 2026-09-24: left to read raw
    fields, the model swapped two athletes' percentiles, called 47% vs 48%
    "slightly better", and flipped who gains more ground. Every comparison
    is therefore spelled out in both directions."""
    facts: list[str] = []
    projections = list(result.athletes)
    if len(projections) == 2 and result.head_to_head:
        a, b = projections
        pair = result.head_to_head[0]
        b_ahead = 1 - float(pair["probability_a_finishes_ahead"])
        leader, trailer = (b, a) if b_ahead >= 0.5 else (a, b)
        ahead = max(b_ahead, 1 - b_ahead)
        diff = a.probability_holds_or_improves - b.probability_holds_or_improves
        if abs(diff) < 0.05:
            hold = (
                "They have about the same chance to hold or improve their "
                f"standing ({_pct(a.probability_holds_or_improves)} vs "
                f"{_pct(b.probability_holds_or_improves)})"
            )
        else:
            better, worse = (a, b) if diff > 0 else (b, a)
            hold = (
                f"{better.athlete_name} has the better chance to hold or improve "
                f"their standing ({_pct(better.probability_holds_or_improves)} vs "
                f"{_pct(worse.probability_holds_or_improves)})"
            )
        facts.append(
            f"BOTTOM LINE: {hold}. {leader.athlete_name} is "
            f"{_likelihood(ahead)} to finish ahead of {trailer.athlete_name} "
            f"at Meet {result.to_meet} ({_pct(ahead)})."
        )
    elif len(projections) == 1:
        p = projections[0]
        facts.append(
            f"BOTTOM LINE: {p.athlete_name} is "
            f"{_likelihood(p.probability_holds_or_improves)} to hold or improve "
            f"their standing by Meet {p.to_meet} "
            f"({_pct(p.probability_holds_or_improves)}), most likely finishing "
            f"about {_ordinal(p.expected_place)}."
        )
    for p in projections:
        now = p.current
        place = (
            f"{_ordinal(now.place_overall)} of {now.field_size}"
            if now.place_overall
            else f"of {now.field_size}"
        )
        low, high = p.place_range_80
        facts.append(
            f"{p.athlete_name}: {place} at {now.season_year} Meet {now.meet_number} "
            f"(beat {round(now.percentile)}% of the field). Projected at Meet "
            f"{p.to_meet}: about {_ordinal(p.expected_place)} (80% range "
            f"{_ordinal(low)}-{_ordinal(high)}, "
            f"percentile {round(p.expected_percentile)}). "
            f"Chance to hold or improve that standing: "
            f"{_pct(p.probability_holds_or_improves)} "
            f"({_likelihood(p.probability_holds_or_improves)})."
        )
    for p in projections:
        facts.extend(_history_lines(p.athlete_name, p.history))
    for h in result.head_to_head:
        a_ahead = float(h["probability_a_finishes_ahead"])
        a_gains = float(h["probability_a_gains_more_ground"])
        facts.append(
            f"Finishing ahead at Meet {result.to_meet}: {h['athlete_a']} "
            f"{_pct(a_ahead)}, {h['athlete_b']} {_pct(1 - a_ahead)}."
        )
        facts.append(
            f"Gaining more ground on the field: {h['athlete_a']} {_pct(a_gains)}, "
            f"{h['athlete_b']} {_pct(1 - a_gains)} "
            f"({_likelihood(max(a_gains, 1 - a_gains))} either way)."
        )
    for athlete_id, reason in result.unavailable.items():
        facts.append(f"No projection for {athlete_id}: {reason}.")
    return facts


def compact_row(r: ResultRow) -> dict[str, Any]:
    """A result shaped for the model: every number already formatted, so it
    never converts ms or seconds itself, plus the raw values for charts."""
    return {
        "athlete": r.row.athlete_display_name,
        "athlete_id": r.row.athlete_id,
        "school": r.row.school_display_name,
        "season_year": r.row.season_year,
        "meet_number": r.row.meet_number,
        "meet_name": r.row.meet_name,
        "race": f"{r.row.division_code} {r.row.gender_code}",
        "distance_m": r.row.distance_meters,
        "place": r.row.place_overall,
        "time": _clock(r.row.finish_time_ms),
        "time_s": None if r.row.finish_time_ms is None else r.row.finish_time_ms / 1000,
        "pace": _pace(r.pace_seconds_per_mile),
        "pace_s_per_mi": None
        if r.pace_seconds_per_mile is None
        else round(r.pace_seconds_per_mile, 2),
        "grade": r.row.grade,
    }


def build_analytics_tools(
    canonical: CanonicalReadRepository,
    *,
    publication_id: str,
    emit: EventSink | None = None,
    python_executor: PythonExecutor | None = None,
) -> list[Any]:
    """Build the tool set bound to one pinned snapshot connection.

    ``canonical`` must be opened over a verified, read-only, immutable
    connection. ``emit`` receives UI events (charts, images, code runs,
    follow-up suggestions); without it the presentation tools still work
    but their output is only visible in the transcript.
    ``python_executor`` enables ``run_python_analysis_tool``; it is left out
    of the tool list entirely when no sandbox is configured.
    """

    field_cache: dict[str, dict[str, list[RaceStanding]]] = {}

    def field_standings() -> dict[str, list[RaceStanding]]:
        """Every athlete's standing in every race, built once per turn."""
        if "all" not in field_cache:
            field_cache["all"] = build_field(canonical.list_result_rows()).by_athlete
        return field_cache["all"]

    def _emit(event: dict[str, Any]) -> None:
        if emit is not None:
            emit(event)

    @tool
    def list_dimensions_tool() -> dict[str, Any]:
        """List the seasons, divisions, meet numbers, grades, and school/
        athlete counts in the currently pinned dataset. Answers "how many
        athletes/schools" directly."""
        return {**_asdict(list_dimensions(canonical)), "publication_id": publication_id}

    @tool
    def find_athletes_tool(query: str) -> dict[str, Any]:
        """Search athletes by a case-insensitive substring of their name.
        Always use this first to turn a name into an athlete_id.

        Args:
            query: Part of an athlete's name, e.g. "Walker" or "Audrey Walker".
        """
        matches = find_athletes(canonical, query, limit=25)
        return {
            "matches": [
                {"athlete_id": m.athlete_id, "display_name": m.display_name}
                for m in matches
            ],
            "publication_id": publication_id,
        }

    @tool
    def find_schools_tool(query: str) -> dict[str, Any]:
        """Search schools by a case-insensitive substring of their name.
        Pass an empty string to list every school.

        Args:
            query: Part of a school's name, e.g. "James".
        """
        matches = find_schools(canonical, query, limit=200)
        return {
            # Counted here, not by the model: found 2026-09-24, it counted
            # this 28-item list as 29.
            "count": len(matches),
            "matches": [
                {"school_id": m.school_id, "display_name": m.display_name}
                for m in matches
            ],
            "publication_id": publication_id,
        }

    @tool
    def get_athlete_profile_tool(athlete_id: str) -> dict[str, Any]:
        """An athlete's full profile: school and grade by season, career
        bests, every race in chronological order (time, pace, place), and
        standing_in_field for every race -- place, share of the field beaten
        (percentile), and time as a ratio of the race median. Use standing,
        not raw time, to judge improvement between meets: course conditions
        swing whole fields (the 2025 JV boys' median pace moved 31% within
        one season). The raw pace trend is included but is course-dependent.

        Args:
            athlete_id: The athlete_id from find_athletes_tool.
        """
        profile = get_athlete_profile(canonical, athlete_id)
        if profile is None:
            return {
                "error": "no athlete with that id",
                "publication_id": publication_id,
            }
        trend = profile.pace_trend
        return {
            "athlete_id": athlete_id,
            "display_name": profile.athlete.display_name,
            "seasons": [_asdict(s) for s in profile.seasons],
            "summary": {
                **_asdict(profile.summary),
                "best_time": _clock(profile.summary.best_time_ms),
                "best_pace": _pace(profile.summary.best_pace_seconds_per_mile),
                "latest_pace": _pace(profile.summary.latest_pace_seconds_per_mile),
            },
            "results": [compact_row(r) for r in profile.results],
            "standing_in_field": [
                _standing_row(st) for st in field_standings().get(athlete_id, [])
            ],
            "raw_pace_trend_course_dependent": _asdict(trend) if trend else None,
            "publication_id": publication_id,
        }

    @tool
    def project_standing_tool(
        athlete_ids: list[str], season_year: int, to_meet: int = 3
    ) -> dict[str, Any]:
        """Project where athletes will stand in the field at a later meet,
        with 80% ranges and probabilities. Use this for EVERY question about
        who will improve, chances, predictions, or projections -- never fit
        your own trend or extrapolate times (a straight-line time projection
        once produced a 3:56 mile for a 6th grader).

        "Improve" means standing in the field (place and share of the field
        beaten), not raw time, unless the user explicitly asks about time.
        The model looks at how standing actually changed for the past
        athletes who started nearest this one, backtested on held-out
        seasons: standing is sticky, especially at the front (past Meet 1
        winners finished 1st-3rd at Meet 3 every time).
        Lead with key_facts' BOTTOM LINE, and state every number and every
        "likely / coin flip / unlikely" word exactly as key_facts has it --
        they are pre-computed; never re-derive, flip, or embellish them.
        Probabilities between 40% and 60% are a coin flip everywhere in the
        answer: never call them likely, unlikely, or an edge for either side.
        Then give each athlete's projected percentile, likely place, and 80%
        range. Describe past seasons only as key_facts' history lines state
        them (a move from 6th to 8th is a slip, not an improvement).
        "probability_holds_or_improves_standing" is the chance of finishing
        at or above their latest standing; for a race leader that means
        holding the lead. The model treats each athlete independently; for
        a two-athlete question, also get their actual head-to-head record
        (compare_athletes_tool) and mention it. Quote the backtest in
        percentile points, and say plainly that it is a projection, not a
        certainty.

        Args:
            athlete_ids: One to five athlete_ids from find_athletes_tool.
            season_year: The season to project within, e.g. 2026.
            to_meet: The meet to project to (default 3).
        """
        if not 1 <= len(athlete_ids) <= 5:
            return {"error": "pass between 1 and 5 athlete_ids"}
        result = project_standing(
            canonical, athlete_ids, season_year=season_year, to_meet=to_meet
        )
        model = result.model
        return {
            "key_facts": _projection_sentences(result),
            "season_year": season_year,
            "to_meet": to_meet,
            "athletes": [
                {
                    "athlete": p.athlete_name,
                    "athlete_id": p.athlete_id,
                    "division": p.division_code,
                    "latest_race": _standing_row(p.current),
                    "already_ran_target_meet": _standing_row(p.already_raced)
                    if p.already_raced
                    else None,
                    "projected_percentile": p.expected_percentile,
                    "projected_percentile_range_80": list(p.percentile_range_80),
                    "projected_place_in_similar_field": p.expected_place,
                    "projected_place_range_80": list(p.place_range_80),
                    "probability_holds_or_improves_standing": (
                        p.probability_holds_or_improves
                    ),
                    "standing_history": [_standing_row(h) for h in p.history],
                }
                for p in result.athletes
            ],
            "head_to_head": [
                {
                    f"{h['athlete_a']} finishes ahead of {h['athlete_b']} at "
                    f"Meet {to_meet}": h["probability_a_finishes_ahead"],
                    f"{h['athlete_a']} gains more ground on the field than "
                    f"{h['athlete_b']}": h["probability_a_gains_more_ground"],
                }
                for h in result.head_to_head
            ],
            "unavailable": result.unavailable,
            "model": {
                "version": result.model_version,
                "method": "change in standing among the 80 past athletes "
                "who started nearest this one",
                "training_athletes": model.training_pairs if model else 0,
                "training_seasons": list(model.training_seasons) if model else [],
                "does_not_use": "an athlete's own trajectory, consistency, "
                "grade, or head-to-head record -- only where they stand now and "
                "how past athletes who stood there moved. Never attribute other "
                "reasoning to the model.",
                "how_to_read": "Percentile = share of the field beaten "
                "(higher is better); places assume a field the size of the "
                "athlete's latest race. In backtests the typical miss is about "
                "10 percentile points (about 3.5 for the top 5%), only a little "
                "better than assuming standing stays put -- the model's value "
                "is its calibrated ranges and probabilities, not a sharper "
                "point forecast. Say so when it matters.",
            },
            "backtest_held_out_seasons": [_asdict(b) for b in result.backtest],
            "publication_id": publication_id,
        }

    @tool
    def compare_athletes_tool(athlete_ids: list[str]) -> dict[str, Any]:
        """Compare two to five athletes: each one's summary and bests, every
        race they ran together with each athlete's result, and a head-to-head
        record for each pair (who finished ahead).

        Args:
            athlete_ids: Two to five athlete_ids from find_athletes_tool.
        """
        if not 2 <= len(athlete_ids) <= 5:
            return {"error": "pass between 2 and 5 athlete_ids"}
        comparison = compare_athletes(canonical, athlete_ids)
        names = {
            p.athlete.athlete_id: p.athlete.display_name for p in comparison.profiles
        }
        return {
            "athletes": [
                {
                    "athlete_id": p.athlete.athlete_id,
                    "display_name": p.athlete.display_name,
                    "schools_by_season": {
                        s.season_year: s.school_display_name for s in p.seasons
                    },
                    "races": p.summary.races,
                    "best_time": _clock(p.summary.best_time_ms),
                    "best_pace": _pace(p.summary.best_pace_seconds_per_mile),
                    "latest_pace": _pace(p.summary.latest_pace_seconds_per_mile),
                    "best_place": p.summary.best_place,
                    "results": [compact_row(r) for r in p.results],
                }
                for p in comparison.profiles
            ],
            "missing_athlete_ids": comparison.missing_athlete_ids,
            "shared_races": [
                {
                    "season_year": s.season_year,
                    "meet_number": s.meet_number,
                    "race": f"{s.division_code} {s.gender_code}",
                    "entries": [
                        {
                            "athlete": names.get(e.athlete_id, e.athlete_id),
                            "place": e.place_overall,
                            "time": _clock(e.finish_time_ms),
                            "pace": _pace(e.pace_seconds_per_mile),
                        }
                        for e in s.entries
                    ],
                }
                for s in comparison.shared_races
            ],
            "head_to_head": [
                {
                    "athlete_a": names.get(h.athlete_a_id),
                    "athlete_b": names.get(h.athlete_b_id),
                    "shared_races": h.races,
                    "a_finished_ahead": h.a_ahead,
                    "b_finished_ahead": h.b_ahead,
                }
                for h in comparison.head_to_head
            ],
            "publication_id": publication_id,
        }

    @tool
    def get_leaderboards_tool(
        season_year: int | None = None,
        school_id: str | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
        meet_numbers: list[int] | None = None,
        grades: list[int] | None = None,
    ) -> dict[str, Any]:
        """Headline metrics and leaderboards for a filtered slice: athlete/
        school/meet/result counts, the 10 fastest paces, the top-10 placements,
        and the 10 most improved athletes (first vs latest pace, 2+ races).
        This is the dashboard overview; use it for "fastest", "most improved",
        and "how many" questions before writing SQL.

        Args:
            season_year: One season, e.g. 2025. Omit for all seasons.
            school_id: One school (from find_schools_tool). Omit for all.
            division_code: "2nd Grade", "Frosh", "JV", or "Varsity". Omit for all.
            gender_code: "F" or "M". Omit for both.
            meet_numbers: Limit to these meet numbers, e.g. [1, 2]. Omit for all.
            grades: Limit to these grades, e.g. [7, 8]. Omit for all.
        """
        overview = get_overview(
            canonical,
            ResultFilters(
                season_year=season_year,
                school_id=school_id,
                division_code=division_code,
                gender_code=gender_code,
                meet_numbers=tuple(meet_numbers or ()),
                grades=tuple(grades or ()),
            ),
        )
        return {
            "metrics": _asdict(overview.metrics),
            "fastest_pace": [compact_row(r) for r in overview.fastest_pace],
            "top_placements": [compact_row(r) for r in overview.top_placements],
            "most_improved": [
                {
                    **_asdict(e),
                    "first_pace": _pace(e.first_pace_seconds_per_mile),
                    "latest_pace": _pace(e.latest_pace_seconds_per_mile),
                }
                for e in overview.most_improved
            ],
            "publication_id": publication_id,
        }

    @tool
    def list_results_tool(
        season_year: int | None = None,
        school_id: str | None = None,
        athlete_id: str | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
        meet_numbers: list[int] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Individual race results for a filtered slice, chronological then
        by place, each with formatted time and pace plus raw seconds. Use it
        to see a race's finishers or to get rows for a chart.

        Args:
            season_year: One season. Omit for all.
            school_id: One school. Omit for all.
            athlete_id: One athlete. Omit for all.
            division_code: "2nd Grade", "Frosh", "JV", or "Varsity". Omit for all.
            gender_code: "F" or "M". Omit for both.
            meet_numbers: Limit to these meet numbers. Omit for all.
            limit: Maximum rows to return (1-150, default 50).
        """
        rows = list_results(
            canonical,
            ResultFilters(
                season_year=season_year,
                school_id=school_id,
                athlete_id=athlete_id,
                division_code=division_code,
                gender_code=gender_code,
                meet_numbers=tuple(meet_numbers or ()),
            ),
        )
        capped = max(1, min(limit, MAX_RESULT_ROWS))
        return {
            "total_rows": len(rows),
            "rows": [compact_row(r) for r in rows[:capped]],
            "truncated": len(rows) > capped,
            "publication_id": publication_id,
        }

    @tool
    def get_team_scores_tool(
        season_year: int | None = None,
        meet_number: int | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
        school_id: str | None = None,
    ) -> dict[str, Any]:
        """Official cross-country team scores: the sum of each school's five
        best overall places (lower wins; a school needs five placed
        finishers to score), with team_rank within each race. A "team win"
        is team_rank 1. summary_by_school already counts team wins,
        podiums, and races scored per school -- use it instead of counting
        entries yourself.

        Args:
            season_year: One season, e.g. 2025. Omit for all seasons.
            meet_number: One meet within the season. Omit for all meets.
            division_code: "2nd Grade", "Frosh", "JV", or "Varsity". Omit for all.
            gender_code: "F" or "M". Omit for both.
            school_id: One school (rank stays its rank in the full race).
        """
        entries = get_team_scores(
            canonical,
            season_year=season_year,
            meet_number=meet_number,
            division_code=division_code,
            gender_code=gender_code,
            school_id=school_id,
        )
        return {
            "summary_by_school": _team_summary(entries),
            "entries": [_asdict(e) for e in entries],
            "publication_id": publication_id,
        }

    @tool
    def get_saint_sebastian_standings_tool(
        season_year: int | None = None,
        division_code: str | None = None,
        gender_code: str | None = None,
    ) -> dict[str, Any]:
        """Saint Sebastian Award standings: lowest cumulative finish time
        among athletes who ran every meet that season so far (three meets
        make the award final). Includes standing_rank and time_back_ms.

        Args:
            season_year: One season, e.g. 2025. Omit for all seasons.
            division_code: "2nd Grade", "Frosh", "JV", or "Varsity". Omit for all.
            gender_code: "F" or "M". Omit for both.
        """
        entries = get_saint_sebastian_standings(
            canonical,
            season_year=season_year,
            division_code=division_code,
            gender_code=gender_code,
        )
        return {
            "entries": [
                {
                    **_asdict(e),
                    "cumulative_time": _clock(e.cumulative_time_ms),
                    "time_back": _clock(e.time_back_ms),
                }
                for e in entries
            ],
            "publication_id": publication_id,
        }

    @tool
    def team_score_what_if_tool(
        season_year: int,
        meet_number: int,
        division_code: str,
        gender_code: str,
        remove_athlete_ids: list[str] | None = None,
        add_runners: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Re-score one race under a hypothetical change and return both the
        actual and the scenario team standings. Removing a runner moves
        everyone behind them up a place; adding a runner at a finish time
        moves everyone slower down a place. Uses the official scoring rule.
        This is a scenario -- always say so when you report it.

        Args:
            season_year: The race's season.
            meet_number: The race's meet number.
            division_code: "2nd Grade", "Frosh", "JV", or "Varsity".
            gender_code: "F" or "M".
            remove_athlete_ids: athlete_ids to take out of the race.
            add_runners: Hypothetical runners, each {"school_id": str,
                "finish_time_s": number, "label": str}.
        """
        extras: list[HypotheticalRunner] = []
        for runner in add_runners or []:
            try:
                extras.append(
                    HypotheticalRunner(
                        school_id=str(runner["school_id"]),
                        finish_time_ms=int(float(runner["finish_time_s"]) * 1000),
                        label=str(runner.get("label", "hypothetical runner")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                return {
                    "error": "each add_runners item needs school_id and finish_time_s"
                }
        scenario = team_score_scenario(
            canonical,
            season_year=season_year,
            meet_number=meet_number,
            division_code=division_code,
            gender_code=gender_code,
            remove_athlete_ids=tuple(remove_athlete_ids or ()),
            add_runners=tuple(extras),
        )
        return {
            **_asdict(scenario),
            "note": "Scenario only; official scores are unchanged.",
            "publication_id": publication_id,
        }

    @tool
    def calculate_tool(expression: str) -> dict[str, Any]:
        """Exact arithmetic. Use for every calculation instead of doing math
        yourself: differences, averages, percentages, projections. Supports
        + - * / // % **, parentheses, abs round min max sqrt log exp floor
        ceil mean median stdev sum, pi, e. Race times may be written as
        quoted clock strings and are converted to seconds, e.g.
        '"16:36.70" - "15:31"' or 'mean("8:23", "8:11", "8:45")'. The result
        comes back as a number and, read as seconds, as a clock time.

        Args:
            expression: The arithmetic expression to evaluate.
        """
        try:
            return calculate(expression)
        except CalculatorError as error:
            return {"error": str(error), "expression": expression}

    @tool
    def build_chart_tool(
        chart_type: str,
        title: str,
        data: list[dict[str, Any]],
        x: str,
        y: str,
        color: str | None = None,
        x_is_category: bool = True,
        y_format: str = "number",
        x_title: str | None = None,
        y_title: str | None = None,
    ) -> dict[str, Any]:
        """Show the user an interactive chart. Pass rows you got from other
        tools -- never invented numbers. Use it whenever a trend, comparison,
        or ranking is easier to see than read (progression over races,
        athletes side by side, team scores). The chart appears in the chat
        by itself; afterwards just refer to it, don't describe every point.

        Args:
            chart_type: "line" (change over races/seasons), "bar" (ranking or
                comparison), "scatter", or "area".
            title: Short chart title.
            data: Up to 500 rows of {field: number or string}.
            x: Field for the x axis (e.g. "race" or "athlete").
            y: Numeric field for the y axis.
            color: Optional field that splits series (e.g. "athlete").
            x_is_category: True for labels/races (kept in the given order),
                false for a numeric x.
            y_format: "number", "clock_seconds" (y is a time in seconds),
                "pace_seconds_per_mile", or "place". Time, pace, and place
                axes are drawn with better values at the top.
            x_title: Optional axis title.
            y_title: Optional axis title.
        """
        try:
            spec = build_chart_spec(
                chart_type=chart_type,
                title=title,
                data=data,
                x=x,
                y=y,
                color=color,
                x_is_category=x_is_category,
                y_format=y_format,
                x_title=x_title,
                y_title=y_title,
            )
        except ChartSpecError as error:
            return {"error": str(error)}
        _emit({"type": "chart", "title": title, "spec": spec})
        return {"rendered": True, "title": title, "points": len(data)}

    @tool
    def suggest_follow_ups_tool(questions: list[str]) -> dict[str, Any]:
        """Offer the user two to four short follow-up questions they can click,
        each answerable with this data. Call once, as your last step.

        Args:
            questions: Two to four follow-up questions, each under 80 characters.
        """
        cleaned = [q.strip()[:100] for q in questions if q and q.strip()][
            :MAX_FOLLOW_UPS
        ]
        _emit({"type": "follow_ups", "questions": cleaned})
        return {"shown": len(cleaned)}

    def run_readonly_query_tool(sql: str) -> dict[str, Any]:
        try:
            result = run_readonly_query(
                canonical.connection,
                sql,
                allowed_tables=DEFAULT_ALLOWED_TABLES,
                max_rows=DEFAULT_MAX_ROWS,
                max_seconds=DEFAULT_MAX_SECONDS,
            )
        except QueryRefusedError as refusal:
            return {"refused": str(refusal), "publication_id": publication_id}
        return {**_asdict(result), "publication_id": publication_id}

    run_readonly_query_tool.__doc__ = SQL_TOOL_DOC

    tools: list[Any] = [
        list_dimensions_tool,
        find_athletes_tool,
        find_schools_tool,
        get_athlete_profile_tool,
        compare_athletes_tool,
        project_standing_tool,
        get_leaderboards_tool,
        list_results_tool,
        get_team_scores_tool,
        get_saint_sebastian_standings_tool,
        team_score_what_if_tool,
        calculate_tool,
        tool(run_readonly_query_tool),
        build_chart_tool,
        suggest_follow_ups_tool,
    ]

    if python_executor is not None:
        executor = python_executor

        def run_python_analysis_tool(sql: str, code: str) -> dict[str, Any]:
            if len(code) > MAX_PYTHON_CODE_CHARS:
                return {
                    "error": "code is too long -- never paste data into the code; "
                    "select it with `sql` and read data.csv instead"
                }
            try:
                result = run_readonly_query(
                    canonical.connection,
                    sql,
                    allowed_tables=DEFAULT_ALLOWED_TABLES,
                    max_rows=MAX_PYTHON_DATA_ROWS,
                    max_seconds=DEFAULT_MAX_SECONDS,
                )
            except QueryRefusedError as refusal:
                return {"refused": str(refusal)}
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(result.columns)
            writer.writerows(result.rows)
            try:
                outcome = executor.run(code, {"data.csv": buffer.getvalue()})
            except Exception as error:
                return {"error": f"sandbox unavailable: {type(error).__name__}"}
            _emit(
                {
                    "type": "code",
                    "language": "python",
                    "code": code,
                    "sql": sql,
                    "stdout": outcome.stdout,
                    "stderr": outcome.stderr,
                    "ok": outcome.ok,
                }
            )
            for name, png in outcome.images:
                _emit({"type": "image", "title": name, "src": image_data_uri(png)})
            return {
                "ok": outcome.ok,
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
                "data_rows": result.row_count,
                "data_truncated": result.truncated,
                "chart_shown": bool(outcome.images),
                "note": None
                if outcome.images or CHART_FILENAME not in code
                else "chart.png was not produced",
            }

        run_python_analysis_tool.__doc__ = PYTHON_TOOL_DOC
        tools.append(tool(run_python_analysis_tool))

    return tools
