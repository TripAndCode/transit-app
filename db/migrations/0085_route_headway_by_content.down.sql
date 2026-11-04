-- DESTRUCTIVE: drops route_headway_by_content. The medians of timetables no
-- longer loaded are lost for good; the loaded one is recomputed by the next
-- analyze, and older dates then measure long gaps against it.
DROP TABLE IF EXISTS route_headway_by_content;
