-- The transfers a planner watches at a hub: riders leaving route A's trips at
-- one of from_stop_ids and boarding route B's at one of to_stop_ids, with
-- walk_sec to change stops. The two sides may be different operators, so each
-- names its own agency and its own stop ids; no place is matched across
-- operators. A NULL direction takes both of the route's directions.
--
-- day_types lists the day types (pipeline.day_types.fallback_day_type) the
-- pair is read on. Reliability is computed when read
-- (pipeline.reports.transfers), so nothing else stores per-pair figures.
-- Admins write the table through api/routers/admin_transfers.py, which caps
-- the number of pairs and validates the agencies, routes and stops.
CREATE TABLE IF NOT EXISTS transfer_pairs (
    id               SERIAL      PRIMARY KEY,
    name             TEXT        NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    from_agency_id   INTEGER     NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    from_route_code  TEXT        NOT NULL,
    from_direction   SMALLINT    CHECK (from_direction IN (0, 1)),
    from_stop_ids    TEXT[]      NOT NULL CHECK (cardinality(from_stop_ids) BETWEEN 1 AND 8),
    to_agency_id     INTEGER     NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    to_route_code    TEXT        NOT NULL,
    to_direction     SMALLINT    CHECK (to_direction IN (0, 1)),
    to_stop_ids      TEXT[]      NOT NULL CHECK (cardinality(to_stop_ids) BETWEEN 1 AND 8),
    walk_sec         INTEGER     NOT NULL DEFAULT 120 CHECK (walk_sec BETWEEN 0 AND 1800),
    day_types        TEXT[]      NOT NULL DEFAULT ARRAY['weekday', 'saturday', 'sunday_holiday']
        CHECK (cardinality(day_types) >= 1
               AND day_types <@ ARRAY['weekday', 'saturday', 'sunday_holiday']),
    active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The pair list a report screen reads is the pairs touching its agency.
CREATE INDEX IF NOT EXISTS idx_transfer_pairs_from_agency ON transfer_pairs (from_agency_id);
CREATE INDEX IF NOT EXISTS idx_transfer_pairs_to_agency ON transfer_pairs (to_agency_id);
