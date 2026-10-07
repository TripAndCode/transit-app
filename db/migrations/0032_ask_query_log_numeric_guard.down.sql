-- DESTRUCTIVE: drops ask_query_log.numeric_guard_triggered.
ALTER TABLE ask_query_log DROP COLUMN IF EXISTS numeric_guard_triggered;
