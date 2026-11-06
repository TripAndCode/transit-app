-- One row per weekly live scorecard: the document the VPS workbench publishes to
-- R2 (evaluations/<run_date>/live.json), keyed by its run date. A live week can
-- exist without a summary, so it is its own table rather than a column of
-- ml_evaluations.
CREATE TABLE IF NOT EXISTS ml_live_evaluations (
    run_date       DATE         PRIMARY KEY,
    schema_version SMALLINT     NOT NULL,
    document       JSONB        NOT NULL,
    ingested_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
