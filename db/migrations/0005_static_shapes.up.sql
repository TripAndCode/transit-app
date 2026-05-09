CREATE TABLE IF NOT EXISTS static_shapes (
    agency_id INT NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    shape_id  TEXT NOT NULL,
    geom      geometry(LineString, 4326) NOT NULL,
    PRIMARY KEY (agency_id, shape_id)
);

CREATE INDEX IF NOT EXISTS idx_static_shapes_geom
    ON static_shapes USING GIST (geom);
