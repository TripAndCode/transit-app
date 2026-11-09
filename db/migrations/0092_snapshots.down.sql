-- DESTRUCTIVE: drops every stored snapshot; the figures they froze were read
-- from aggregates that have been rebuilt since, so nothing can restore them.
DROP TABLE IF EXISTS snapshots;
