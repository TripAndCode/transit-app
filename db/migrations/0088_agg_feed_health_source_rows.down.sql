-- Not marked DESTRUCTIVE: agg_feed_health is a derived aggregate, and the
-- dropped columns are rewritten by the next analyze() run of each agency (the
-- same convention 0067 follows for total_rows).
ALTER TABLE agg_feed_health
    DROP COLUMN IF EXISTS archive_rows,
    DROP COLUMN IF EXISTS live_rows;
