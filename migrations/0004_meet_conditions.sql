-- Migration 0004: meet dates and venues, race start times, and race-day
-- weather (2026-10-18, owner request).
--
-- Source: RunSignup's results Overview page ("Race Day Weather"), whose
-- data comes from /Race/Results/Overview/Weather?raceId=&raceEventDaysId=.
-- That feed is modeled hourly weather at the venue's coordinates (an
-- Open-Meteo-style forecast/reanalysis), not a weather-station measurement;
-- `source` records this so analysis never mistakes it for an observation.
--
-- This is metadata. No result changes, so frozen seasons (R1.8) keep their
-- results exactly; filling their dates, venues, and weather is covered by the
-- 2026-10-18 R1.8 amendment.

-- Venue coordinates as RunSignup publishes them for the weather feed.
ALTER TABLE meets ADD COLUMN venue_latitude REAL;
ALTER TABLE meets ADD COLUMN venue_longitude REAL;

-- Local wall-clock times in the venue's time zone (ISO 8601, no offset);
-- races within one meet can start at different times (2024 Meet 2: 8:30-9:55).
ALTER TABLE races ADD COLUMN start_time TEXT;
ALTER TABLE races ADD COLUMN end_time TEXT;

CREATE TABLE meet_weather_hourly (
    meet_id                   TEXT NOT NULL REFERENCES meets(meet_id),
    observed_at               TEXT NOT NULL,  -- local hour, 'YYYY-MM-DDTHH:MM'
    timezone                  TEXT NOT NULL,  -- e.g. 'America/New_York'
    utc_offset_seconds        INTEGER NOT NULL,
    temperature_f             REAL,
    relative_humidity_pct     REAL CHECK (relative_humidity_pct IS NULL
                                          OR relative_humidity_pct BETWEEN 0 AND 100),
    wind_speed_mph            REAL CHECK (wind_speed_mph IS NULL OR wind_speed_mph >= 0),
    cloud_cover_pct           REAL CHECK (cloud_cover_pct IS NULL
                                          OR cloud_cover_pct BETWEEN 0 AND 100),
    weather_code              INTEGER,        -- WMO weather interpretation code
    weather_description       TEXT,           -- the WMO code in words
    latitude                  REAL NOT NULL,
    longitude                 REAL NOT NULL,
    elevation_m               REAL,
    source                    TEXT NOT NULL,  -- see the header comment
    source_race_id            TEXT NOT NULL,
    source_race_event_days_id TEXT NOT NULL,
    raw_sha256                TEXT NOT NULL,  -- of the raw response as retrieved
    raw_s3_key                TEXT,           -- archived raw response, if uploaded
    retrieved_at              TEXT NOT NULL,  -- UTC
    PRIMARY KEY (meet_id, observed_at, source)
);

-- The weather nearest each race's start: the lookup a performance analysis
-- joins on. Starts round to the nearest hour ('09:20' uses the 09:00
-- reading, '09:55' the 10:00 one).
CREATE VIEW v_race_start_weather AS
SELECT
    r.race_id,
    r.meet_id,
    r.division_code,
    r.gender_code,
    r.start_time,
    w.observed_at,
    w.temperature_f,
    w.relative_humidity_pct,
    w.wind_speed_mph,
    w.cloud_cover_pct,
    w.weather_code,
    w.weather_description,
    w.source
FROM races r
JOIN meet_weather_hourly w
  ON w.meet_id = r.meet_id
 AND w.observed_at = strftime('%Y-%m-%dT%H:00', r.start_time, '+30 minutes');
