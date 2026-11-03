-- DESTRUCTIVE: drops agg_table_state. Every agency's next analyze then finds no
-- table state and rebuilds every table in full.
DROP TABLE IF EXISTS agg_table_state;
