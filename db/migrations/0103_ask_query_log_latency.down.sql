-- DESTRUCTIVE: drops ask_query_log.latency_ms, the measured latency of each card dispatch and follow-up; it cannot be reconstructed.
ALTER TABLE ask_query_log DROP COLUMN IF EXISTS latency_ms;
