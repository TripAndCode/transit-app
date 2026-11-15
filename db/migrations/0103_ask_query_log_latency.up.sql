-- 0103_ask_query_log_latency.up.sql
-- Wall-clock milliseconds of a notebook card's dispatch, or of a follow-up's
-- model call, so the usage signal can show how long each takes as well as how
-- often it runs. Nullable: /ask rows and rows written before this column carry
-- no latency, which is distinct from a measured zero.

ALTER TABLE ask_query_log
  ADD COLUMN IF NOT EXISTS latency_ms INTEGER;
