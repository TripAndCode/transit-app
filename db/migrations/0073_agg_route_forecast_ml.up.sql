-- One row per upcoming (agency, route, service_date, hour): the latest
-- published ML forecast for that slot. No origin_date in the primary key --
-- a fresher forecast for the same future slot upserts over an older one, so
-- this table only ever holds each slot's current forecast, not a history of
-- every day it was predicted.
CREATE TABLE IF NOT EXISTS agg_route_forecast_ml (
    agency_id     INT          NOT NULL REFERENCES agencies(agency_id),
    route_code    TEXT         NOT NULL,
    service_date  DATE         NOT NULL,
    hour          SMALLINT     NOT NULL,
    mean_min      REAL         NOT NULL,
    q10           REAL         NOT NULL,
    q50           REAL         NOT NULL,
    q90           REAL         NOT NULL,
    model_version TEXT         NOT NULL,
    origin_date   DATE         NOT NULL,
    generated_at  TIMESTAMPTZ  NOT NULL,
    PRIMARY KEY (agency_id, route_code, service_date, hour)
);
