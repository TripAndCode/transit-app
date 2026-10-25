-- Not destructive: every row is re-ingested from the evaluations/ prefix in
-- R2, which keeps every week's summary.
DROP TABLE IF EXISTS ml_evaluations;
