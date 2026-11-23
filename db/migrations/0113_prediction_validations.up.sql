-- Each agency's predicted delays held against the passages its vehicles were
-- observed making, over a range of service dates, as
-- scripts/validate_vp_predictions.py measured them and
-- scripts/record_prediction_validation.py recorded them. The counts are kept,
-- not percentages, so the on-time rates and their difference are computed from
-- one source when read.
--
-- stop_events are the kept stop events of the range and compared those with an
-- observed passage. kept_on_time and observed_on_time count the compared events
-- on time by their kept and by their observed delay, under the on-time window
-- the tolerances name (early_tolerance_sec NULL: on time however early).
-- report_schema is the report's SCHEMA, which changes when the method does.
-- A second record of the same agency and dates replaces the first.
CREATE TABLE IF NOT EXISTS prediction_validations (
    agency_id            INTEGER     NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    from_date            DATE        NOT NULL,
    to_date              DATE        NOT NULL,
    report_schema        SMALLINT    NOT NULL CHECK (report_schema > 0),
    early_tolerance_sec  INTEGER     CHECK (early_tolerance_sec >= 0),
    late_tolerance_sec   INTEGER     NOT NULL CHECK (late_tolerance_sec >= 0),
    stop_events          INTEGER     NOT NULL,
    compared             INTEGER     NOT NULL,
    kept_on_time         INTEGER     NOT NULL,
    observed_on_time     INTEGER     NOT NULL,
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agency_id, from_date, to_date),
    CHECK (from_date <= to_date),
    CHECK (compared > 0 AND compared <= stop_events),
    CHECK (kept_on_time BETWEEN 0 AND compared AND observed_on_time BETWEEN 0 AND compared)
);
