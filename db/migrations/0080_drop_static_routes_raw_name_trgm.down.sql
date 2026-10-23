-- Back to 0012's definitions (rebuilt from table data; nothing is lost).
CREATE INDEX IF NOT EXISTS idx_static_routes_short_name_trgm
    ON static_routes USING gin (route_short_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_static_routes_long_name_trgm
    ON static_routes USING gin (route_long_name gin_trgm_ops);
