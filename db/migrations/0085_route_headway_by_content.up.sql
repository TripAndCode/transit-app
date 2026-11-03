-- Each archived timetable's scheduled headway median per route: the threshold
-- agg_route_headway_daily measures long gaps against, for the dates whose rows
-- were ingested against that timetable (pipeline.static_headways). Computed
-- from the Postgres timetable while it is loaded, by agg_route_headway's SQL at
-- its registry version (logic_version), and never deleted: a timetable no
-- longer loaded cannot be recomputed. Every route of the timetable's dominant
-- services has a row, NULL where it gives no median, so a computed content is
-- never computed again at the same version.
CREATE TABLE IF NOT EXISTS route_headway_by_content (
    agency_id                     INT     NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    content_hash                  TEXT    NOT NULL,
    route_code                    TEXT    NOT NULL,
    logic_version                 INT     NOT NULL,
    scheduled_headway_median_sec  REAL,
    PRIMARY KEY (agency_id, content_hash, route_code, logic_version)
);
