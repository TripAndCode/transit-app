-- DESTRUCTIVE: drops planned_trips_daily. A saved day whose timetable has since
-- been replaced cannot be captured again, so its planned trips are lost and
-- the service-delivered rate stops counting it.
DROP TABLE IF EXISTS planned_trips_daily;
