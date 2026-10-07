-- Not marked DESTRUCTIVE: agg_feed_health is a derived aggregate, and the
-- dropped column is rewritten by the next analyze() run of each agency (the
-- same convention 0049 follows for agg_meta.static_fingerprint).
ALTER TABLE agg_feed_health DROP COLUMN IF EXISTS total_rows;
