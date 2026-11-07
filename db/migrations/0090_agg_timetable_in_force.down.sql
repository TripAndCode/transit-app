-- DESTRUCTIVE: drops agg_timetable_in_force. Every agency's next analyze then
-- finds no versions in force recorded and rebuilds every date some version is
-- in force over.
DROP TABLE IF EXISTS agg_timetable_in_force;
