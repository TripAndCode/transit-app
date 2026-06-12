CREATE TABLE IF NOT EXISTS agg_stop_daily (
    agency_id    INTEGER NOT NULL REFERENCES agencies(agency_id),
    stop_id      TEXT    NOT NULL,
    date         DATE    NOT NULL,
    service_type TEXT    NOT NULL,
    time_band    TEXT    NOT NULL,
    delay_sum    BIGINT  NOT NULL,
    samples      INTEGER NOT NULL,
    PRIMARY KEY (agency_id, stop_id, date, service_type, time_band)
);
CREATE INDEX IF NOT EXISTS idx_agg_stop_daily_agency_date ON agg_stop_daily (agency_id, date);

CREATE TABLE IF NOT EXISTS agg_stop_routes (
    agency_id   INTEGER NOT NULL REFERENCES agencies(agency_id),
    stop_id     TEXT    NOT NULL,
    route_codes TEXT    NOT NULL,
    PRIMARY KEY (agency_id, stop_id)
);
