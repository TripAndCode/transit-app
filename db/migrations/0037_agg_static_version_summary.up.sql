-- Per-static-feed-version headline supply figures: how many trips
-- (`trips.txt` rows) and how many vehicle-kilometers ONE full run of the
-- entire defined schedule represents, for whichever `static_trips.
-- static_version_id` (see migration 0036) that version stamped on its rows.
--
-- This is NOT a date-range total the way `agg_service_delivered_daily`'s
-- planned-trip count is -- it describes the schedule DEFINITION itself
-- (one row per trip once), independent of how many calendar days a caller
-- later asks about. That's what makes it comparable across two different
-- static GTFS versions: a real change in the number of scheduled trips (or
-- the shapes they run over) between two feed releases produces two
-- different rows here, keyed by their own static_version_id.
--
-- Unlike every other agg_* table, `pipeline.analyze.analyze()` deliberately
-- does NOT wipe this table for the agency before rewriting -- it UPSERTs
-- only the row for the CURRENTLY loaded static_version_id (same convention
-- as `agg_meta`). `pipeline.static_loader.load_static()` unconditionally
-- DELETEs and replaces `static_trips`/`static_shapes` for the agency on
-- every reload, so a past version's raw static rows are gone the moment a
-- new one is loaded -- this table is the only place a past version's
-- planned-trip-count/vehicle-km survives that replacement, which is what
-- lets a schedule-revision boundary be explained in terms of a real,
-- specific before/after change instead of just a bare date.
--
-- `vehicle_km` is NULL (not zero) when the agency has no `shapes.txt`
-- loaded at all -- a reader must treat NULL as "not computable", never as
-- "zero planned distance". A trip whose own `shape_id` doesn't resolve to a
-- row in `static_shapes` (partial shapes coverage) is simply excluded from
-- the SUM, so a partially-shaped feed still gets a (necessarily
-- undercounted) non-NULL figure rather than NULL outright.
CREATE TABLE IF NOT EXISTS agg_static_version_summary (
    agency_id         INTEGER NOT NULL REFERENCES agencies(agency_id),
    static_version_id TEXT    NOT NULL,
    trip_count        INTEGER NOT NULL,
    vehicle_km        DOUBLE PRECISION,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, static_version_id)
);
