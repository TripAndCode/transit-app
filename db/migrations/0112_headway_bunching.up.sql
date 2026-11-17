-- agg_route_headway_daily counts each (route, date)'s bunched gaps, those
-- shorter than a quarter of the day's own timetable's scheduled median
-- (pipeline.headways.count_bunched), and how many gaps were judged for them:
-- exact counts, so a month pools them by adding. Both are NULL where the
-- day was not judged: its timetable keeps no scheduled headway, the row was
-- built before these columns, or its date lies before the retention floor,
-- which analyze never rebuilds. NULL is unknown, never zero bunching.
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS bunched_count INTEGER;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS bunching_judged INTEGER;
