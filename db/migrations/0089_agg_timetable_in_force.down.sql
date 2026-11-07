-- Not marked DESTRUCTIVE: agg_timetable_in_force describes what analyze last
-- built. Without it, the next run of an agency finds no versions in force
-- recorded and rebuilds every date some version is in force over.
DROP TABLE IF EXISTS agg_timetable_in_force;
