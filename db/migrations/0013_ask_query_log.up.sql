-- 0013_ask_query_log.up.sql
-- Anonymized per-question analytics log for the Ask tab. Powers router /
-- golden-set tuning + cost analysis. Deliberately has NO user_id / session /
-- IP — it answers "what is asked and how well is it served", not "who asked".
-- 90-day retention via gtfs_pipeline.py prune_query_log.

CREATE TABLE IF NOT EXISTS ask_query_log (
    id           BIGSERIAL PRIMARY KEY,
    agency_id    INTEGER     NOT NULL REFERENCES agencies(agency_id),
    question     TEXT        NOT NULL,
    router_stage TEXT        NOT NULL,
    tool         TEXT,
    success      BOOLEAN     NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ask_query_log_agency_at
    ON ask_query_log (agency_id, created_at);
