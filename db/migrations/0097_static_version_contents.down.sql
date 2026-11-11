-- DESTRUCTIVE: drops static_version_contents. ClickHouse's static_version_map
-- still holds every mapping, so scripts/fill_version_contents.py --apply
-- refills it once the migration is applied again. Until then the stamp names
-- its timetables with no content hash, and load_static, analyze and
-- scripts/backfill_timetable_archive.py --apply refuse to run
-- (pipeline.static_archive.require_mirror).
DROP TABLE IF EXISTS static_version_contents;
