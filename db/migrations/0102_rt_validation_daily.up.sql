-- One row per agency and service date: the daily GTFS-RT validator summary
-- scripts/rt_validate_sample.py publishes to R2 for one sampled hour of the
-- day's TripUpdates, as pipeline.rt_validation_ingest reads it. summary is
-- the whole document; status, reason and the two file counts are copied out
-- of it. etag is the object's R2 ETag, so an unchanged summary is not fetched
-- again. ingested_at moves only when what the scorecard reads changes: the
-- status, files_validated or the rules.
CREATE TABLE IF NOT EXISTS rt_validation_daily (
    agency_id        INT          NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    service_date     DATE         NOT NULL,
    status           TEXT         NOT NULL CHECK (status IN ('ok', 'no_archive', 'no_timetable', 'validator_failed')),
    reason           TEXT,
    files_given      INT          NOT NULL CHECK (files_given >= 0),
    files_validated  INT          NOT NULL CHECK (files_validated BETWEEN 0 AND files_given),
    summary          JSONB        NOT NULL,
    etag             TEXT         NOT NULL,
    ingested_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, service_date),
    CHECK ((reason IS NULL) = (status = 'ok'))
);

-- The feed-quality scorecard's validator figures over each month's window
-- (pipeline.feed_quality). Days with no summary, or whose validator could not
-- run, are not validated: without an ok day the polls and rules are NULL
-- beside validator_missing, never a 0 that would read as no findings. A month
-- built before the columns existed holds none of them until analyze rebuilds
-- it, which the registry's version for the table requires.
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS validator_ok_days INT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS validator_failed_days INT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS validator_files_validated BIGINT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS validator_rules JSONB;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS validator_missing TEXT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS validator_ingested_at TIMESTAMPTZ;
ALTER TABLE feed_quality_monthly ADD CONSTRAINT feed_quality_monthly_validator_check
    CHECK (
        (validator_ok_days IS NULL AND validator_failed_days IS NULL AND validator_files_validated IS NULL
         AND validator_rules IS NULL AND validator_missing IS NULL AND validator_ingested_at IS NULL)
        OR (validator_ok_days IS NOT NULL AND validator_failed_days IS NOT NULL
            AND validator_ok_days >= 0 AND validator_failed_days >= 0
            AND (validator_missing IS NULL) = (validator_ok_days > 0)
            AND (validator_rules IS NULL) = (validator_missing IS NOT NULL)
            AND (validator_files_validated IS NULL) = (validator_missing IS NOT NULL)
            AND (validator_ingested_at IS NULL) = (validator_ok_days + validator_failed_days = 0))
    );
