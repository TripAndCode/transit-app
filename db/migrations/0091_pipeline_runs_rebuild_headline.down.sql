-- DESTRUCTIVE: drops every analyze run's headline before and after its rebuild;
-- the rows it was read from have been rebuilt since, so nothing can restore it.
ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS rebuild_headline;
