"""Race-day weather parsing and loading (ingest/weather.py, migration 0004).

The fixture is RunSignup's real weather feed for 2023 Meet 2 (race 154767,
Telegraph Rd, Alexandria), whose Overview page reads "Drizzle, 63F" at 1 PM.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.ingest.weather import (
    SOURCE,
    MeetDay,
    PendingLoad,
    WeatherPayloadError,
    apply_all,
    apply_meet_weather,
    meet_days_from_overview,
    parse_weather,
    superseded,
)
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "runsignup_weather"
    / "154767-2023-280813.json"
)
DAY = MeetDay("154767", 2023, 2, "280813")
DIVISIONS = ("2nd Grade", "Frosh", "JV", "Varsity")


@pytest.fixture
def payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_writer_connection(tmp_path / "xc.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _seed_meet(conn: sqlite3.Connection, season: int = 2023, meet: int = 2) -> str:
    writer = HistoricalCanonicalWriter(conn)
    meet_id: str = writer.get_or_create_meet(
        season_year=season, meet_number=meet, name=f"Meet {meet}", series="NVJCYO"
    )
    for division in DIVISIONS:
        for gender in ("F", "M"):
            writer.get_or_create_race(
                meet_id=meet_id,
                division_code=division,
                gender_code=gender,
                distance_meters=2000,
            )
    return meet_id


def _apply(conn: sqlite3.Connection, payload: dict[str, Any]) -> Any:
    return apply_meet_weather(
        conn,
        DAY,
        parse_weather(payload),
        venue="6601 Telegraph Rd, Alexandria, VA 22310",
        raw_sha256="ab" * 32,
        raw_s3_key="raw/runsignup-weather/2023/x.json",
        retrieved_at="2026-10-18T15:00:00Z",
    )


def test_parse_reads_hours_location_and_every_race(payload: dict[str, Any]) -> None:
    weather = parse_weather(payload)
    assert [h.observed_at for h in weather.hours][:3] == [
        "2023-10-14T11:00",
        "2023-10-14T12:00",
        "2023-10-14T13:00",
    ]
    one_pm = weather.hours[2]
    assert one_pm.weather_code == 53  # "Drizzle", as the Overview page shows
    assert round(one_pm.temperature_f or 0) == 63
    assert (round(weather.latitude, 3), round(weather.longitude, 3)) == (
        38.770,
        -77.108,
    )
    assert weather.timezone == "America/New_York"
    assert len(weather.races) == 8
    assert {(r.division, r.gender_code) for r in weather.races} == {
        (d, g) for d in DIVISIONS for g in ("F", "M")
    }


def test_parse_rejects_an_unexpected_unit(payload: dict[str, Any]) -> None:
    payload["results"]["hourly_units"]["temperature_2m"] = "°C"
    with pytest.raises(WeatherPayloadError, match="temperature unit"):
        parse_weather(payload)


def test_parse_rejects_ragged_columns(payload: dict[str, Any]) -> None:
    payload["results"]["hourly"]["temperature_2m"].pop()
    with pytest.raises(WeatherPayloadError, match="temperature_2m"):
        parse_weather(payload)


def test_apply_writes_date_venue_race_times_and_hourly_rows(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    meet_id = _seed_meet(conn)

    summary = _apply(conn, payload)

    assert (summary.meet_id, summary.hours_written, summary.races_timed) == (
        meet_id,
        5,
        8,
    )
    assert summary.events_without_race == ()
    assert summary.unparsed_events == ()
    meet = conn.execute(
        "SELECT meet_date, venue, venue_latitude FROM meets WHERE meet_id = ?",
        (meet_id,),
    ).fetchone()
    assert meet[0] == "2023-10-14"
    assert meet[1].startswith("6601 Telegraph Rd")
    assert round(meet[2], 3) == 38.770
    starts = {
        row[0]
        for row in conn.execute(
            "SELECT start_time FROM races WHERE meet_id = ?", (meet_id,)
        )
    }
    assert starts == {"2023-10-14T13:00:00"}


def test_start_weather_view_pairs_each_race_with_its_start_hour(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    _seed_meet(conn)
    _apply(conn, payload)

    rows = conn.execute(
        "SELECT observed_at, weather_description, source FROM v_race_start_weather"
    ).fetchall()

    assert len(rows) == 8
    assert {tuple(r) for r in rows} == {("2023-10-14T13:00", "Drizzle", SOURCE)}


def test_apply_is_idempotent(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    _seed_meet(conn)
    _apply(conn, payload)
    _apply(conn, payload)
    assert conn.execute("SELECT COUNT(*) FROM meet_weather_hourly").fetchone()[0] == 5


def test_apply_never_creates_a_missing_meet(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    summary = _apply(conn, payload)
    assert summary.skipped_reason == "meet not in database yet"
    assert conn.execute("SELECT COUNT(*) FROM meets").fetchone()[0] == 0


def test_apply_reports_events_with_no_matching_race(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    meet_id = _seed_meet(conn)
    conn.execute(
        "DELETE FROM races WHERE meet_id = ? AND division_code = '2nd Grade'",
        (meet_id,),
    )
    summary = _apply(conn, payload)
    assert summary.races_timed == 6
    assert sorted(summary.events_without_race) == ["2nd Boys", "2nd Girls"]
    assert summary.unparsed_events == ()


def test_the_2023_meet_2_placeholder_race_is_superseded() -> None:
    assert superseded("154708", 2023) is True
    assert superseded("154767", 2023) is False
    assert superseded("154708", 2024) is False
    assert superseded("154050", 2023) is False


def test_meet_days_come_from_the_overview_page() -> None:
    infos = {
        "2024": {"1": {"raceEventDaysId": 313918}, "2": {"raceEventDaysId": 313918}},
        "2023": {"3": {"raceEventDaysId": 279177}},
    }
    html = (
        '<script type="application/json" id="resultsOverviewIndividualResultSetInfos">'
        f"{json.dumps(infos)}</script>"
    )
    assert meet_days_from_overview(html, "154050") == [
        MeetDay("154050", 2023, 1, "279177"),
        MeetDay("154050", 2024, 1, "313918"),
    ]
    with pytest.raises(WeatherPayloadError, match="not a known meet race"):
        meet_days_from_overview(html, "999999")


def test_an_unrecognizable_event_name_is_a_problem(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    _seed_meet(conn)
    payload["raceCategories"][0]["eventName"] = "Fun Run"
    summary = _apply(conn, payload)
    assert summary.unparsed_events == ("Fun Run",)
    assert summary.races_timed == 7


def test_start_weather_uses_the_nearest_hour(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    meet_id = _seed_meet(conn)
    _apply(conn, payload)
    for start, expected in [
        ("2023-10-14T12:20:00", "2023-10-14T12:00"),
        ("2023-10-14T12:55:00", "2023-10-14T13:00"),
        ("2023-10-14T13:30:00", "2023-10-14T14:00"),
    ]:
        conn.execute(
            "UPDATE races SET start_time = ? WHERE meet_id = ?", (start, meet_id)
        )
        hours = {
            r[0] for r in conn.execute("SELECT observed_at FROM v_race_start_weather")
        }
        assert hours == {expected}, start


def _pending(payload: dict[str, Any], day: MeetDay = DAY) -> PendingLoad:
    return PendingLoad(
        day=day,
        weather=parse_weather(payload),
        venue="6601 Telegraph Rd, Alexandria, VA 22310",
        raw_sha256="ab" * 32,
        raw_s3_key=None,
        retrieved_at="2026-10-18T15:00:00Z",
    )


def test_apply_all_is_all_or_nothing(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    """Found in review: the writer connection autocommits, so a failure
    midway used to leave a meet's old weather deleted and others half
    written. A bad reading must now roll back every change."""
    meet_id = _seed_meet(conn)
    [first] = apply_all(conn, [_pending(payload)])
    assert first.hours_written == 5
    good = conn.execute(
        "SELECT observed_at, temperature_f FROM meet_weather_hourly ORDER BY 1"
    ).fetchall()

    other_meet = _seed_meet(conn, season=2024, meet=1)
    bad = json.loads(json.dumps(payload))
    bad["results"]["hourly"]["relative_humidity_2m"][-1] = 101  # violates CHECK
    bad["raceCategories"][0]["startTime"] = "2023-10-14T09:00:00"
    with pytest.raises(sqlite3.IntegrityError):
        apply_all(
            conn,
            [
                _pending(payload, MeetDay("154050", 2024, 1, "1")),  # would succeed
                _pending(bad),  # fails on its last row
            ],
        )

    assert (
        conn.execute(
            "SELECT observed_at, temperature_f FROM meet_weather_hourly ORDER BY 1"
        ).fetchall()
        == good
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM meet_weather_hourly WHERE meet_id = ?",
            (other_meet,),
        ).fetchone()[0]
        == 0
    )
    other = conn.execute(
        "SELECT meet_date FROM meets WHERE meet_id = ?", (other_meet,)
    ).fetchone()
    assert other[0] is None
    assert {
        r[0]
        for r in conn.execute(
            "SELECT start_time FROM races WHERE meet_id = ?", (meet_id,)
        )
    } == {"2023-10-14T13:00:00"}
    assert not conn.in_transaction


def test_the_venue_records_where_it_came_from(
    conn: sqlite3.Connection, payload: dict[str, Any]
) -> None:
    meet_id = _seed_meet(conn)
    source = "runsignup race 154767: raw/runsignup-race/154767-3bf051c4add8f3e8.json"
    apply_meet_weather(
        conn,
        DAY,
        parse_weather(payload),
        venue="6601 Telegraph Rd, Alexandria, VA 22310",
        venue_source=source,
        raw_sha256="ab" * 32,
        raw_s3_key=None,
        retrieved_at="2026-10-18T15:00:00Z",
    )
    row = conn.execute(
        "SELECT venue, venue_source FROM meets WHERE meet_id = ?", (meet_id,)
    ).fetchone()
    assert (row[0], row[1]) == ("6601 Telegraph Rd, Alexandria, VA 22310", source)
