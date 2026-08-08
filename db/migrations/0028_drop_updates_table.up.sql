-- Raw `updates` moved to ClickHouse (see pipeline/clickhouse.py,
-- db/clickhouse/schema.sql). Every application read/write of raw updates
-- has been on ClickHouse since PR #176; this table has taken no writes and
-- served no reads since then. Dropping it now, after that PR ran in
-- production, per the coexistence-then-drop plan from the migration design.
--
-- Data is not gone: the ClickHouse table holds the full backfilled history
-- (including agencies whose Postgres ingest had silently stopped before
-- this migration), and can be re-derived from raw_archives/ if ever needed.
DROP TABLE IF EXISTS updates;
