"""Race-day weather and meet metadata from RunSignup (2026-10-18).

RunSignup's results Overview page shows a "Race Day Weather" box. It is
drawn in the browser from a JSON feed,
``/Race/Results/Overview/Weather?raceId=&raceEventDaysId=``, that returns
hourly modeled weather at the venue's coordinates (Open-Meteo-style:
temperature in F, relative humidity, 10 m wind in mph, cloud cover, and a
WMO weather code) plus each race's start and end time. The page itself
lists each season's ``raceEventDaysId`` in an embedded JSON island.

This module only parses those payloads and writes them into the canonical
database (migration 0004); fetching lives in scripts/load_race_weather.py.
Nothing here touches results: dates, venues, start times, and weather are
metadata, which the 2026-10-18 R1.8 amendment allows for frozen seasons.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from xc_platform.ingest.adapters.runsignup import (
    FROZEN_SEASON_EXCEPTIONS,
    MEET_RACE_IDS,
    classify_event,
)

SOURCE = "runsignup-overview-weather"
OVERVIEW_URL = "https://runsignup.com/Race/Results/Overview/{race_id}"
WEATHER_URL = "https://runsignup.com/Race/Results/Overview/Weather"

# WMO weather interpretation codes, as used by Open-Meteo.
WMO_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

_YEAR_INFOS_RE = re.compile(
    r'id="resultsOverviewIndividualResultSetInfos">(.*?)</script>', re.S
)


class WeatherPayloadError(ValueError):
    """A RunSignup page or feed did not have the expected shape."""


@dataclass(frozen=True, slots=True)
class MeetDay:
    race_id: str
    season_year: int
    meet_number: int
    race_event_days_id: str


@dataclass(frozen=True, slots=True)
class RaceWindow:
    event_name: str
    division: str | None
    gender_code: str | None
    start_time: str  # local, 'YYYY-MM-DDTHH:MM:SS'
    end_time: str | None


@dataclass(frozen=True, slots=True)
class HourlyReading:
    observed_at: str  # local, 'YYYY-MM-DDTHH:MM'
    temperature_f: float | None
    relative_humidity_pct: float | None
    wind_speed_mph: float | None
    cloud_cover_pct: float | None
    weather_code: int | None


@dataclass(frozen=True, slots=True)
class MeetWeather:
    latitude: float
    longitude: float
    elevation_m: float | None
    timezone: str
    utc_offset_seconds: int
    hours: tuple[HourlyReading, ...]
    races: tuple[RaceWindow, ...]


def superseded(race_id: str, season_year: int) -> bool:
    """True when another race is the authoritative source for this season's
    meet: 154708 carries empty 2023 Meet 2 placeholders whose weather is for
    Reston, while the meet ran in Alexandria under race 154767."""
    meet = MEET_RACE_IDS.get(race_id)
    return any(
        other != race_id and year == season_year and MEET_RACE_IDS.get(other) == meet
        for other, year in FROZEN_SEASON_EXCEPTIONS
    )


def meet_days_from_overview(html: str, race_id: str) -> list[MeetDay]:
    """Every (season, raceEventDaysId) the Overview page knows for a race."""
    meet_number = MEET_RACE_IDS.get(race_id)
    if meet_number is None:
        raise WeatherPayloadError(f"race {race_id} is not a known meet race")
    match = _YEAR_INFOS_RE.search(html)
    if match is None:
        raise WeatherPayloadError(f"race {race_id}: no result-set data on the page")
    infos: dict[str, dict[str, Any]] = json.loads(match.group(1))
    days: list[MeetDay] = []
    for year, sets in sorted(infos.items()):
        for days_id in sorted({str(s["raceEventDaysId"]) for s in sets.values()}):
            days.append(MeetDay(race_id, int(year), meet_number, days_id))
    return days


def _column(hourly: dict[str, Any], name: str, length: int) -> list[Any]:
    values = hourly.get(name)
    if values is None:
        return [None] * length
    if len(values) != length:
        raise WeatherPayloadError(
            f"hourly.{name} has {len(values)} values, not {length}"
        )
    return list(values)


def parse_weather(payload: dict[str, Any]) -> MeetWeather:
    if not payload.get("success", True):
        raise WeatherPayloadError("RunSignup reported success=false")
    results = payload.get("results") or {}
    hourly = results.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise WeatherPayloadError("no hourly weather in the payload")
    units = results.get("hourly_units") or {}
    if units.get("temperature_2m", "°F") != "°F":
        raise WeatherPayloadError(
            f"unexpected temperature unit {units['temperature_2m']!r}"
        )
    if units.get("wind_speed_10m", "mp/h") not in ("mp/h", "mph"):
        raise WeatherPayloadError(f"unexpected wind unit {units['wind_speed_10m']!r}")
    n = len(times)
    temp = _column(hourly, "temperature_2m", n)
    rh = _column(hourly, "relative_humidity_2m", n)
    wind = _column(hourly, "wind_speed_10m", n)
    cloud = _column(hourly, "cloud_cover", n)
    code = _column(hourly, "weather_code", n)
    hours = tuple(
        HourlyReading(
            observed_at=str(times[i])[:16],
            temperature_f=temp[i],
            relative_humidity_pct=rh[i],
            wind_speed_mph=wind[i],
            cloud_cover_pct=cloud[i],
            weather_code=None if code[i] is None else int(code[i]),
        )
        for i in range(n)
    )
    races = []
    for category in payload.get("raceCategories") or []:
        division, gender = classify_event(str(category.get("eventName") or ""))
        races.append(
            RaceWindow(
                event_name=str(category.get("eventName") or ""),
                division=division,
                gender_code=gender,
                start_time=str(category["startTime"]),
                end_time=category.get("endTime"),
            )
        )
    return MeetWeather(
        latitude=float(results["latitude"]),
        longitude=float(results["longitude"]),
        elevation_m=None
        if results.get("elevation") is None
        else float(results["elevation"]),
        timezone=str(results.get("timezone") or ""),
        utc_offset_seconds=int(results.get("utc_offset_seconds") or 0),
        hours=hours,
        races=tuple(races),
    )


@dataclass(frozen=True, slots=True)
class ApplySummary:
    meet_id: str | None
    hours_written: int
    races_timed: int
    # Recognized events with no race in the database -- scheduled but never
    # published, like 2025 Meet 1's 2nd Grade races. Informational.
    events_without_race: tuple[str, ...]
    # Events whose name didn't map to a division and gender: a real problem.
    unparsed_events: tuple[str, ...] = ()
    skipped_reason: str | None = None


def apply_meet_weather(
    conn: sqlite3.Connection,
    day: MeetDay,
    weather: MeetWeather,
    *,
    venue: str | None,
    raw_sha256: str,
    raw_s3_key: str | None,
    retrieved_at: str,
) -> ApplySummary:
    """Write one meet-day's date, venue, race times, and hourly weather.

    Idempotent: re-running replaces this source's rows for the meet. Never
    creates a meet -- one that isn't in the database yet (2023 Meet 2 before
    its import) is skipped and reported.
    """
    row = conn.execute(
        "SELECT meet_id FROM meets WHERE season_year = ? AND meet_number = ?",
        (day.season_year, day.meet_number),
    ).fetchone()
    if row is None:
        return ApplySummary(None, 0, 0, (), skipped_reason="meet not in database yet")
    meet_id = str(row[0])
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    starts = sorted(r.start_time for r in weather.races)
    conn.execute(
        "UPDATE meets SET meet_date = ?, venue = COALESCE(?, venue), "
        "venue_latitude = ?, venue_longitude = ?, updated_at = ? WHERE meet_id = ?",
        (
            starts[0][:10] if starts else weather.hours[0].observed_at[:10],
            venue,
            weather.latitude,
            weather.longitude,
            now,
            meet_id,
        ),
    )
    races = {
        (division, gender): race_id
        for race_id, division, gender in conn.execute(
            "SELECT race_id, division_code, gender_code FROM races WHERE meet_id = ?",
            (meet_id,),
        )
    }
    timed, without_race, unparsed = 0, [], []
    for window in weather.races:
        if window.division is None or window.gender_code is None:
            unparsed.append(window.event_name)
            continue
        race_id = races.get((window.division, window.gender_code))
        if race_id is None:
            without_race.append(window.event_name)
            continue
        conn.execute(
            "UPDATE races SET start_time = ?, end_time = ?, updated_at = ? "
            "WHERE race_id = ?",
            (window.start_time, window.end_time, now, race_id),
        )
        timed += 1
    conn.execute(
        "DELETE FROM meet_weather_hourly WHERE meet_id = ? AND source = ?",
        (meet_id, SOURCE),
    )
    conn.executemany(
        "INSERT INTO meet_weather_hourly (meet_id, observed_at, timezone, "
        "utc_offset_seconds, temperature_f, relative_humidity_pct, wind_speed_mph, "
        "cloud_cover_pct, weather_code, weather_description, latitude, longitude, "
        "elevation_m, source, source_race_id, source_race_event_days_id, raw_sha256, "
        "raw_s3_key, retrieved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?)",
        [
            (
                meet_id,
                h.observed_at,
                weather.timezone,
                weather.utc_offset_seconds,
                h.temperature_f,
                h.relative_humidity_pct,
                h.wind_speed_mph,
                h.cloud_cover_pct,
                h.weather_code,
                None
                if h.weather_code is None
                else WMO_DESCRIPTIONS.get(h.weather_code),
                weather.latitude,
                weather.longitude,
                weather.elevation_m,
                SOURCE,
                day.race_id,
                day.race_event_days_id,
                raw_sha256,
                raw_s3_key,
                retrieved_at,
            )
            for h in weather.hours
        ],
    )
    return ApplySummary(
        meet_id, len(weather.hours), timed, tuple(without_race), tuple(unparsed)
    )
