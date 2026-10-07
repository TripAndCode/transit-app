-- DESTRUCTIVE: drops ask_query_log and every logged question.
-- 0013_ask_query_log.down.sql
DROP TABLE IF EXISTS ask_query_log;
