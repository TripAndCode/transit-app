-- DESTRUCTIVE: drops static_shapes and every loaded shape.
DROP INDEX IF EXISTS idx_static_shapes_geom;
DROP TABLE IF EXISTS static_shapes;
