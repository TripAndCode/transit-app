-- Each timetable version name's content, as ClickHouse's static_version_map
-- last maps it (pipeline.static_archive), so the definition stamp names each
-- timetable in force by a short hash of its content
-- (pipeline.reports.definition) with no ClickHouse read per request. Written
-- beside each map row and never ahead of it; a name the map does not hold has
-- no row, and the stamp shows it with no hash. scripts/fill_version_contents.py
-- fills it from the map.
CREATE TABLE IF NOT EXISTS static_version_contents (
    agency_id          INT         NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    static_version_id  TEXT        NOT NULL,
    content_hash       TEXT        NOT NULL,
    archived_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (agency_id, static_version_id)
);
