ALTER TABLE agg_route_daily DROP COLUMN IF EXISTS sum_delay_sec;
ALTER TABLE agg_daily_trend DROP COLUMN IF EXISTS sum_delay_sec;
ALTER TABLE agg_route_hour_dow DROP COLUMN IF EXISTS sum_delay_sec;
ALTER TABLE agg_route_dow DROP COLUMN IF EXISTS sum_delay_sec;
ALTER TABLE agg_route_hour DROP COLUMN IF EXISTS sum_delay_sec;
ALTER TABLE agg_route_stats DROP COLUMN IF EXISTS sum_delay_sec;
