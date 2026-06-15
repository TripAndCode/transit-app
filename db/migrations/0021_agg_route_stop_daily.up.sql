CREATE TABLE IF NOT EXISTS agg_route_stop_daily (
    agency_id    INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code   TEXT    NOT NULL,
    stop_id      TEXT    NOT NULL,
    date         DATE    NOT NULL,
    service_type TEXT    NOT NULL,
    time_band    TEXT    NOT NULL,
    delay_sum    BIGINT  NOT NULL,
    samples      BIGINT  NOT NULL,
    PRIMARY KEY (agency_id, route_code, stop_id, date, service_type, time_band)
);
CREATE INDEX IF NOT EXISTS idx_agg_route_stop_daily_agency_route
    ON agg_route_stop_daily (agency_id, route_code);
