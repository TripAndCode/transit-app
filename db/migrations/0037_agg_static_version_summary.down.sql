-- DESTRUCTIVE: drops agg_static_version_summary, whose past static versions'
-- rows analyze cannot rebuild once load_static has replaced their raw tables.
DROP TABLE IF EXISTS agg_static_version_summary;
