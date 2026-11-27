-- agg_route_hour.scheduled_time holds a departure's time on its service day's
-- clock, which runs past 24:00 for a departure after midnight ('24:10:00',
-- '25:05:00'). TIME stops at 24:00:00, so the column is an INTERVAL from the
-- service day's 00:00: every TIME value keeps its meaning, the text the
-- analyze copy writes casts to it unchanged, and EXTRACT(HOUR ...) and the
-- ordering still read it as a clock.
ALTER TABLE agg_route_hour
  ALTER COLUMN scheduled_time TYPE INTERVAL USING scheduled_time::interval;
