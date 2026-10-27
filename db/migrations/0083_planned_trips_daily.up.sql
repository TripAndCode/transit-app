-- Each service day's planned trips, saved while the timetable that plans the
-- day is loaded. The loaded timetable (static_calendar, static_calendar_dates,
-- static_trips) plans only the days its calendar covers, and a reload
-- replaces an agency's calendar, so the days before the new one starts lose
-- their plan the moment it lands. This table keeps them: the service-delivered
-- rate (pipeline.reports.service_delivered) reads a day's saved count before
-- the loaded timetable's.
--
-- pipeline.planned_trips writes it on every analyze run, for the days before
-- today, and before every reload replaces the calendar, for the outgoing
-- timetable's days before the incoming one starts. It inserts only days that
-- have no row yet. The first count saved for a day stands; nothing updates or
-- deletes a row except its agency's deletion. Days before the first capture
-- are not reconstructed: the reload that replaced their timetable erased them.
--
-- One row per agency per day, kept with the other per-day history it is read
-- against (agg_service_delivered_daily), which nothing prunes either.
CREATE TABLE IF NOT EXISTS planned_trips_daily (
    agency_id     INT         NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    service_date  DATE        NOT NULL,
    planned_trips INT         NOT NULL,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, service_date)
);
