-- Planned supply at every stop, per timetable version in force (pipeline.stop_supply):
-- built by analyze from the timetable archive, so a version keeps its rows
-- after a later load replaces it.
--
-- agg_stop_supply_days holds, for each version in force and day type
-- (weekday, saturday, sunday_holiday, by the weekday rule), the typical day
-- its rows are read on. A NULL typical_day is a day type the version cannot
-- tell (no date of that type in force within its calendar, or no calendar
-- kept), which a reader never takes as zero departures. segments is the
-- dates the version was in force on when they were read
-- (pipeline.stop_supply.segments_digest), the same on each of its rows: a
-- run reads its calendar again only once they move. calendar_read says the
-- typical days were read from the version's kept calendar: a version built
-- with none has it false, and its calendar is read once one is kept.
CREATE TABLE IF NOT EXISTS agg_stop_supply_days (
    agency_id         INTEGER NOT NULL REFERENCES agencies(agency_id),
    static_version_id TEXT    NOT NULL,
    content_hash      TEXT    NOT NULL,
    day_type          TEXT    NOT NULL CHECK (day_type IN ('weekday', 'saturday', 'sunday_holiday')),
    typical_day       DATE,
    segments          TEXT    NOT NULL,
    calendar_read     BOOLEAN NOT NULL,
    PRIMARY KEY (agency_id, static_version_id, content_hash, day_type)
);

-- Each stop's departures on that typical day by service-day hour: hour 24
-- and later are the night's after-midnight departures. first_sec and
-- last_sec are the hour's first and last departure in service-day seconds,
-- and max_gap_sec the longest wait between two consecutive ones of the hour,
-- NULL with one. An hour without a departure has no row.
CREATE TABLE IF NOT EXISTS agg_stop_supply_hourly (
    agency_id         INTEGER  NOT NULL REFERENCES agencies(agency_id),
    static_version_id TEXT     NOT NULL,
    content_hash      TEXT     NOT NULL,
    day_type          TEXT     NOT NULL,
    stop_id           TEXT     NOT NULL,
    hour              SMALLINT NOT NULL,
    departures        INTEGER  NOT NULL CHECK (departures > 0),
    first_sec         INTEGER  NOT NULL,
    last_sec          INTEGER  NOT NULL,
    max_gap_sec       INTEGER,
    PRIMARY KEY (agency_id, static_version_id, content_hash, day_type, stop_id, hour)
);
