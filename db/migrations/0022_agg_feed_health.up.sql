CREATE TABLE IF NOT EXISTS agg_feed_health (
    agency_id    INTEGER NOT NULL REFERENCES agencies(agency_id),
    date         DATE    NOT NULL,
    raw_samples  BIGINT  NOT NULL,
    clamp_count  BIGINT  NOT NULL,
    PRIMARY KEY (agency_id, date)
);
