CREATE TABLE IF NOT EXISTS agg_route_hour_dow (
    agency_id    INTEGER  NOT NULL REFERENCES agencies(agency_id),
    route_code   TEXT     NOT NULL,
    service_type TEXT     NOT NULL,
    dow          SMALLINT NOT NULL,
    hour         SMALLINT NOT NULL,
    avg_min      REAL,
    samples      INTEGER,
    PRIMARY KEY (agency_id, route_code, service_type, dow, hour)
);
