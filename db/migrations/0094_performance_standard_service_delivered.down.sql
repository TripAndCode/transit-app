-- Keeps every service-delivered target: the narrower check comes back NOT
-- VALID, so it binds new rows only and a rollback loses nothing an admin set.
-- A release before 0094 reads a service_delivered_pct row as a vehicle-km
-- standard and refuses the standards editor's whole payload while one exists,
-- so a rollback that outlasts a deploy deletes those rows by hand.
ALTER TABLE route_performance_standards DROP CONSTRAINT route_performance_standards_service_delivered_pct_range;
ALTER TABLE route_performance_standards DROP CONSTRAINT route_performance_standards_metric_type_check;
ALTER TABLE route_performance_standards ADD CONSTRAINT route_performance_standards_metric_type_check
    CHECK (metric_type IN ('ewt_sec', 'vehicle_km_delivered_pct', 'on_time_pct')) NOT VALID;
