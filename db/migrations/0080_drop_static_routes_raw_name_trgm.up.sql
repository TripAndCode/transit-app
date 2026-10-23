-- 0012's trigram indexes cover the raw route_short_name and route_long_name.
-- Every route-name match (pipeline/query/schema_linker.resolve_route and
-- describe_data's routes filter) compares normalize(col, NFKC), and the
-- planner uses an index only for a predicate on the indexed expression, so
-- these serve no query while every static reload still maintains them.
--
-- No expression index replaces them: each match is scoped to one agency, and
-- the primary key's leading agency_id narrows the scan to that agency's
-- routes, a set small enough to filter without one. pg_trgm stays installed
-- for resolve_route's similarity() and %.
DROP INDEX IF EXISTS idx_static_routes_short_name_trgm;
DROP INDEX IF EXISTS idx_static_routes_long_name_trgm;
