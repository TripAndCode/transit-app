-- Both indexes are a leading prefix of their table's primary key, so the PK
-- btree already serves every lookup they serve while each costs maintenance on
-- every bulk rebuild/reload:
--   agg_route_stop_daily  PK (agency_id, route_code, stop_id, ...)
--   static_stop_times     PK (agency_id, trip_id, stop_sequence)
DROP INDEX IF EXISTS idx_agg_route_stop_daily_agency_route;
DROP INDEX IF EXISTS idx_sst_trip;
