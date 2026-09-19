-- Recreates the tables' structure only. analyze() no longer builds either, so
-- rolling back here leaves them empty until the matching code is restored too.

CREATE TABLE IF NOT EXISTS agg_route_dow (
    agency_id     int  NOT NULL REFERENCES agencies(agency_id),
    route_code    text NOT NULL,
    service_type  text NOT NULL,
    dow           smallint NOT NULL,
    avg_min       real,
    samples       int,
    sum_delay_sec bigint,
    PRIMARY KEY (agency_id, route_code, service_type, dow),
    CONSTRAINT agg_route_dow_dow_iso_chk CHECK (dow >= 1 AND dow <= 7)
);

CREATE TABLE IF NOT EXISTS agg_stop_seq (
    agency_id     int  NOT NULL REFERENCES agencies(agency_id),
    route_code    text NOT NULL,
    stop_sequence int  NOT NULL,
    stop_name     text,
    avg_min       real,
    samples       int,
    PRIMARY KEY (agency_id, route_code, stop_sequence)
);
