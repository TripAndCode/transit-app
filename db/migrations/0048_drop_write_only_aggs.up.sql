-- agg_route_dow and agg_stop_seq are written by analyze() every run and read
-- by nothing: route_dow_breakdown reads live `updates` so it can honour the
-- requested window, and the per-stop tool surface reads agg_stop_daily /
-- agg_route_stop_daily, which carry a date and a time band this table lacks.
-- Rebuilding them costs a GROUP BY per agency per run for no reader.

DROP TABLE IF EXISTS agg_route_dow;
DROP TABLE IF EXISTS agg_stop_seq;
