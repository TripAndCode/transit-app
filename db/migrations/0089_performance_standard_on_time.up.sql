-- A route's performance standard can set an on-time target: the share of
-- departures on time, in percent, judged on the Routes table's own figure for
-- the route under the request's on-time definition
-- (pipeline.reports.performance_standard). The definition is not stored with
-- the target, because the figure beside it on screen follows the reader's.
ALTER TABLE route_performance_standards DROP CONSTRAINT route_performance_standards_metric_type_check;
ALTER TABLE route_performance_standards ADD CONSTRAINT route_performance_standards_metric_type_check
    CHECK (metric_type IN ('ewt_sec', 'vehicle_km_delivered_pct', 'on_time_pct'));

-- A share is a percentage. The other metrics keep their own units: an excess
-- wait is in seconds. NaN fails the range too, since Postgres orders it above
-- every number.
ALTER TABLE route_performance_standards ADD CONSTRAINT route_performance_standards_on_time_pct_range
    CHECK (metric_type <> 'on_time_pct' OR threshold_value BETWEEN 0 AND 100);
