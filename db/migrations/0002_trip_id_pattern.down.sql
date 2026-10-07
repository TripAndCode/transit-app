-- DESTRUCTIVE: drops agencies.trip_id_pattern and every value in it.
ALTER TABLE agencies DROP COLUMN IF EXISTS trip_id_pattern;
