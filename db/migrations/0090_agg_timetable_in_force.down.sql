-- DESTRUCTIVE: drops agg_timetable_in_force. analyze refuses to run without it
-- until the migration is applied again; the next run after that finds no
-- versions in force recorded and rebuilds every date some version is in force over.
DROP TABLE IF EXISTS agg_timetable_in_force;
