-- GTFS calendar.txt: each service's weekly running days over a date range.
-- With static_calendar_dates (the per-date additions and removals) it says
-- which services run on a date, and so which timetable class the date runs.
CREATE TABLE IF NOT EXISTS static_calendar (
    agency_id  INTEGER NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    service_id TEXT    NOT NULL,
    monday     BOOLEAN NOT NULL,
    tuesday    BOOLEAN NOT NULL,
    wednesday  BOOLEAN NOT NULL,
    thursday   BOOLEAN NOT NULL,
    friday     BOOLEAN NOT NULL,
    saturday   BOOLEAN NOT NULL,
    sunday     BOOLEAN NOT NULL,
    start_date DATE    NOT NULL,
    end_date   DATE    NOT NULL,
    PRIMARY KEY (agency_id, service_id)
);
