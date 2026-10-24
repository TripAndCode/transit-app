-- 0012_pgtrgm_route_names.up.sql
-- Trigram fuzzy match for Japanese route-name aliases. 0080 drops both
-- indexes: resolve_route matches the NFKC-folded columns, which they do not
-- cover. pg_trgm itself stays in use.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_static_routes_short_name_trgm
    ON static_routes USING gin (route_short_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_static_routes_long_name_trgm
    ON static_routes USING gin (route_long_name gin_trgm_ops);
