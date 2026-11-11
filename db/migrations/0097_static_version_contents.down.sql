-- DESTRUCTIVE: drops static_version_contents. ClickHouse's static_version_map
-- still holds every mapping, so scripts/fill_version_contents.py --apply
-- refills it once the migration is applied again; until then the stamp names
-- its timetables with no content hash.
DROP TABLE IF EXISTS static_version_contents;
