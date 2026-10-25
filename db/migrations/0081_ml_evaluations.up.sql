-- One row per weekly ML evaluation run: the summary the VPS workbench
-- publishes to R2 (evaluations/<run_date>/summary.json), keyed by its run
-- date. The whole document is kept; adopted and overall_skill are lifted out
-- so a history list reads them without parsing every document.
CREATE TABLE IF NOT EXISTS ml_evaluations (
    run_date       DATE              PRIMARY KEY,
    schema_version SMALLINT          NOT NULL,
    adopted        BOOLEAN           NOT NULL,
    overall_skill  DOUBLE PRECISION,
    summary        JSONB             NOT NULL,
    ingested_at    TIMESTAMPTZ       NOT NULL DEFAULT now()
);
