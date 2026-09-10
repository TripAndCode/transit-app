-- Durable registry of per-agency, per-field GTFS-RT coverage probe results.
--
-- Every reader that trusts an RT-optional field (stop_id, arr_delay,
-- schedule_relationship_trip, schedule_relationship_stop) gates on this
-- table intersected with `agencies.ingest_strategy` -- see
-- `pipeline.strategies.static_join.rt_field_coverage_confirmed`. Sharing an
-- ingest strategy only means a feed's wire shape matches an already-verified
-- agency's; it does not mean this agency's own live feed populates the
-- optional fields. Storing the verdict here (rather than in a Python
-- constant) means onboarding a newly-verified feed, or retiring one whose
-- feed regressed, is a probe run against the live feed -- not a code change,
-- review cycle, and deploy.
--
-- `confirmed` is the verdict, not the raw fraction: the per-field thresholds
-- that turn a measured fraction into a yes/no live next to the decoder in
-- `pipeline.strategies.static_join.assess_field_coverage`, so a single
-- definition of "populated the way a verified feed populates it" is applied
-- at write time. `coverage` and `sample_size` are kept alongside it for
-- forensics, and are NULL only for a row that predates a measured probe.
--
-- `expires_at` NULL means "does not expire". A probe run stamps a finite
-- expiry so a stale verdict about a feed that has since changed shape
-- eventually stops being trusted; the reader treats an expired row exactly
-- like a missing one ("not available"), never like a refutation.
CREATE TABLE IF NOT EXISTS rt_field_coverage_probes (
    agency_id   INTEGER NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    field_name  TEXT NOT NULL CHECK (field_name IN (
                    'stop_id', 'arr_delay',
                    'schedule_relationship_trip', 'schedule_relationship_stop')),
    confirmed   BOOLEAN NOT NULL,
    coverage    DOUBLE PRECISION CHECK (coverage >= 0.0 AND coverage <= 1.0),
    sample_size INTEGER CHECK (sample_size > 0),
    source_feed TEXT NOT NULL,
    probed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    PRIMARY KEY (agency_id, field_name)
);

-- Carry over the three Hiroshima feeds whose coverage a hardcoded Python
-- set used to assert. Their per-field coverage is independently pinned by
-- `tests/pipeline/test_static_join.py` against checked-in captures of these
-- exact feeds, so they are seeded as non-expiring: the fixtures, not a
-- decaying live probe, are what backs the verdict. A later probe run against
-- one of these feeds upserts over this row, expiry included.
--
-- Matched on each feed's own URL, not on an agency_id: agency_id is a
-- SERIAL, so on a deployment whose agencies were created through the admin
-- API rather than this repo's pinned seed data, ids 8/9/10 are merely the
-- 8th-10th rows inserted and could be any operator. feed_url is UNIQUE and
-- identifies the exact feed whose captures back the verdict, so the
-- carry-over stays exact and every other database is a genuine no-op. The
-- ingest_strategy term keeps a row that has since been repointed at another
-- strategy out of the registry the strategy gate reads.
INSERT INTO rt_field_coverage_probes (agency_id, field_name, confirmed, source_feed, expires_at)
SELECT a.agency_id, f.field_name, TRUE, a.feed_url, NULL
FROM agencies a
CROSS JOIN (VALUES
    ('stop_id'), ('arr_delay'),
    ('schedule_relationship_trip'), ('schedule_relationship_stop')
) AS f(field_name)
WHERE a.feed_url IN (
        'https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin',
        'https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin',
        'https://ajt-mobusta-gtfs.mcapps.jp/realtime/10/trip_updates.bin'
    )
  AND a.ingest_strategy = 'static_join'
ON CONFLICT (agency_id, field_name) DO NOTHING;
