-- A route's performance standard can set a service-delivered target: the share
-- of the route's planned trips the feed did not report as not run, in percent,
-- judged on the route's own figure over the request's dates
-- (pipeline.reports.performance_standard).
ALTER TABLE route_performance_standards DROP CONSTRAINT route_performance_standards_metric_type_check;
ALTER TABLE route_performance_standards ADD CONSTRAINT route_performance_standards_metric_type_check
    CHECK (metric_type IN ('ewt_sec', 'vehicle_km_delivered_pct', 'on_time_pct', 'service_delivered_pct'));

-- A share is a percentage. NaN fails the range too, since Postgres orders it
-- above every number.
ALTER TABLE route_performance_standards ADD CONSTRAINT route_performance_standards_service_delivered_pct_range
    CHECK (metric_type <> 'service_delivered_pct' OR threshold_value BETWEEN 0 AND 100);
