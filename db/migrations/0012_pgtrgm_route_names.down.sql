-- 0012_pgtrgm_route_names.down.sql
DROP INDEX IF EXISTS idx_static_routes_long_name_trgm;
DROP INDEX IF EXISTS idx_static_routes_short_name_trgm;
-- Leave pg_trgm extension installed; other code may depend on it.
