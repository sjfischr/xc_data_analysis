from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from xc_platform.agents.code_interpreter import ExecutionResult
from xc_platform.agents.tools import build_analytics_tools
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def _seed_one_school_five_finishers(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    historical = HistoricalCanonicalWriter(conn)
    source_id = StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    school_id = write.create_school(canonical_name="St Agnes")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    for place in range(1, 6):
        athlete_id = write.create_athlete(display_name=f"Runner {place}")
        historical.insert_result(
            source_id=source_id,
            race_id=race_id,
            athlete_id=athlete_id,
            school_id=school_id,
            ingest_run_id=ingest_run_id,
            finish_time_ms=1_200_000 + place * 1000,
            original_time_text=None,
            place_overall=place,
            bib=None,
            grade=None,
            scored_flag="scored",
        )


def test_get_team_scores_tool_returns_real_data(conn: sqlite3.Connection) -> None:
    _seed_one_school_five_finishers(conn)
    canonical = CanonicalReadRepository(conn)
    tools = {
        t.tool_name: t for t in build_analytics_tools(canonical, publication_id="pub-1")
    }

    result = tools["get_team_scores_tool"](season_year=2026)

    assert result["publication_id"] == "pub-1"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["school_display_name"] == "St Agnes"
    assert result["entries"][0]["score"] == 15


def test_get_team_scores_tool_with_no_filters_still_works(
    conn: sqlite3.Connection,
) -> None:
    _seed_one_school_five_finishers(conn)
    canonical = CanonicalReadRepository(conn)
    tools = {
        t.tool_name: t for t in build_analytics_tools(canonical, publication_id="pub-1")
    }

    result = tools["get_team_scores_tool"]()

    assert len(result["entries"]) == 1


def test_get_saint_sebastian_standings_tool_returns_real_data(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    historical = HistoricalCanonicalWriter(conn)
    source_id = StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    school_id = write.create_school(canonical_name="St Agnes")
    athlete_id = write.create_athlete(display_name="Always Runs")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    historical.insert_result(
        source_id=source_id,
        race_id=race_id,
        athlete_id=athlete_id,
        school_id=school_id,
        ingest_run_id=ingest_run_id,
        finish_time_ms=500_000,
        original_time_text=None,
        place_overall=1,
        bib=None,
        grade=None,
        scored_flag="scored",
    )
    canonical = CanonicalReadRepository(conn)
    tools = {
        t.tool_name: t for t in build_analytics_tools(canonical, publication_id="pub-1")
    }

    result = tools["get_saint_sebastian_standings_tool"](season_year=2026)

    assert result["publication_id"] == "pub-1"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["athlete_display_name"] == "Always Runs"
    assert result["entries"][0]["standing_rank"] == 1


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def run(self, code: str, files: dict[str, str]) -> ExecutionResult:
        self.calls.append((code, files))
        rows = files["data.csv"].strip().count("\n")
        return ExecutionResult(stdout=f"{rows} rows", stderr="", exit_code=0)

    def close(self) -> None:
        pass


def _tools(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]] | None = None,
    executor: _RecordingExecutor | None = None,
) -> dict[str, Any]:
    canonical = CanonicalReadRepository(conn)
    built = build_analytics_tools(
        canonical,
        publication_id="pub-1",
        emit=None if events is None else events.append,
        python_executor=executor,
    )
    return {t.tool_name: t for t in built}


CHART_SQL = (
    "SELECT a.display_name AS athlete, r.place_overall AS place "
    "FROM results r JOIN athletes a ON a.athlete_id = r.athlete_id "
    "ORDER BY r.place_overall"
)


def test_chart_from_sql_skips_the_model_typing_rows(conn: sqlite3.Connection) -> None:
    _seed_one_school_five_finishers(conn)
    events: list[dict[str, Any]] = []

    result = _tools(conn, events)["build_chart_tool"](
        chart_type="bar", title="Places", x="athlete", y="place", sql=CHART_SQL
    )

    assert result == {
        "rendered": True,
        "title": "Places",
        "points": 5,
        "source": "sql",
    }
    [chart] = [e for e in events if e["type"] == "chart"]
    values = chart["spec"]["data"]["values"]
    assert [v["athlete"] for v in values] == [f"Runner {i}" for i in range(1, 6)]


def test_chart_needs_exactly_one_data_source(conn: sqlite3.Connection) -> None:
    chart = _tools(conn)["build_chart_tool"]
    common = {"chart_type": "bar", "title": "t", "x": "a", "y": "b"}
    assert "exactly one" in chart(**common)["error"]
    both = chart(**common, sql="SELECT 1 AS a, 2 AS b", data=[{"a": 1, "b": 2}])
    assert "exactly one" in both["error"]


def test_chart_sql_over_the_cap_is_refused_not_truncated(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_one_school_five_finishers(conn)
    monkeypatch.setattr("xc_platform.agents.tools.MAX_SQL_CHART_ROWS", 3)
    events: list[dict[str, Any]] = []

    result = _tools(conn, events)["build_chart_tool"](
        chart_type="bar", title="t", x="athlete", y="place", sql=CHART_SQL
    )

    assert "more than 3 rows" in result["error"]
    assert not events


def test_chart_from_sql_rejects_writes(conn: sqlite3.Connection) -> None:
    result = _tools(conn)["build_chart_tool"](
        chart_type="bar", title="t", x="a", y="b", sql="DELETE FROM results"
    )
    assert "refused" in result


def test_python_gets_every_row_under_the_cap(conn: sqlite3.Connection) -> None:
    _seed_one_school_five_finishers(conn)
    executor = _RecordingExecutor()

    result = _tools(conn, executor=executor)["run_python_analysis_tool"](
        sql="SELECT result_id, finish_time_ms FROM results", code="print(1)"
    )

    assert result["ok"] and result["data_rows"] == 5
    assert executor.calls[0][1]["data.csv"].strip().count("\n") == 5  # + header


def test_python_refuses_rather_than_analyzing_a_partial_dataset(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_one_school_five_finishers(conn)
    monkeypatch.setattr("xc_platform.agents.tools.MAX_PYTHON_DATA_ROWS", 3)
    executor = _RecordingExecutor()

    result = _tools(conn, executor=executor)["run_python_analysis_tool"](
        sql="SELECT result_id FROM results", code="print(1)"
    )

    assert "more than 3 rows" in result["error"]
    assert "nothing was run" in result["error"]
    assert executor.calls == []
